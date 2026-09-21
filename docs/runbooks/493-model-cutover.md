# #493 调仓在途模型切换 Runbook：旧单盘点 × 人工处置

> 适用场景：把调仓从「配对两腿同状态、同 `trade_date`」的旧模型切到 #493 的
> **单腿确认**模型。**模型切换不写自动数据转换**（决策：不长期兼容双模型），
> 故上线前的存量处置是**合并硬闸门**，须人工完成并留痕。
>
> 本文只提供**只读盘点清单**与处置流程。第 2 节的查询全部只读；第 4–7 节涉及
> 写操作与生产状态，**须另行授权**后由人执行。

---

## 1. 故障机理：为什么旧单不能直接上线

两个模型对「同一个组的腿状态」有**互斥**的不变量，代码只实现新模型：

| | 旧模型（#93） | 新模型（#493） |
| --- | --- | --- |
| 买入创建 | 基金 buy pending **+ CASH sell pending** | 基金 buy pending **+ CASH sell confirmed**（现金日 = 下单日 T） |
| 买入 unconfirm | 两腿一起回 pending | 基金腿回 pending，**扣款腿保持 confirmed**（扣款是既成事实） |
| 卖出创建 | 基金 sell pending + CASH buy pending | **只有基金腿**（组号已分配，无 CASH 腿） |
| 卖出确认 | 两腿一起翻 confirmed | 基金腿 confirmed，**此刻才建** CASH buy（`trade_date` = C，`confirm_date` = A） |
| 快照在途 | 一腿 confirmed、另一腿 confirmed 但日期在未来 | 现金腿已 confirmed、对手腿尚未生效 |

旧模型残留的具体危害：

- **旧买入组的 pending 扣款腿**：新代码读它时，`calculate_available_cash` 仍按
  `trade_date` 把它**扣掉**，但 `_compute_in_transit_amounts` 要求 CASH sell 为
  `confirmed` 才算在途 → 这笔钱从「可用现金」和「市值」里**同时消失**，组合净值
  凭空少一笔。这正是新代码在编辑路径上专门修掉的形态（见[现金账本规则](../reference/business-constraints.md#rule-cash)）。
- **旧卖出组的 pending 到账腿**：新代码认为「pending 的调仓 CASH 腿」不该存在；
  在途口径要求到账腿 `pending`（或 confirmed 且 A > D）——旧腿恰好会被算成在途，
  但它的 `trade_date` 是**下单日 T** 而不是基金确认日 C，金额锚点也是创建期占位
  值而非确认后净额，于是**在途金额是错的**。
- **#471 污染**：调仓基金腿曾在快照后被 auto_confirm 用「不取价、不建腿」的方式
  空确认，留下 `shares`/`price` 为零或缺失的 confirmed 基金腿。**仅重算快照不会
  修复它**——交易不参与快照级联回退（其取价依赖产品行情而非组合快照）。

因此合并前必须确认：**没有旧 pending rebal 腿，也没有未处置的异常组**。

---

## 2. 只读盘点（第 1 步，须授权）

> 在**只读副本**或经批准的只读连接上执行。`LIKE 'rebal\_%'` 的反斜杠是 MySQL
> 默认转义，用于匹配字面下划线。所有查询**不含任何写语句**。

### 2.1 全量 rebal 组清单（含配对现金腿与孤儿组）

这是主清单，**不得只统计 pending 基金腿**——混合状态组与孤儿组同样要暴露。

```sql
SELECT
    t.transfer_group,
    t.portfolio_code,
    COUNT(*)                                            AS legs,
    SUM(t.product_code <> 'CASH')                        AS fund_legs,
    SUM(t.product_code  = 'CASH')                        AS cash_legs,
    GROUP_CONCAT(CONCAT(t.product_code, '/', t.trade_type, '/', t.status)
                 ORDER BY t.id SEPARATOR ' | ')          AS leg_states,
    MIN(t.trade_date)                                    AS min_trade_date,
    MIN(t.confirm_date)                                  AS min_confirm_date,
    MAX(t.confirm_date)                                  AS max_confirm_date,
    SUM(COALESCE(t.actual_amount, t.amount))             AS cash_total
FROM trade t
WHERE t.transfer_group LIKE 'rebal\_%'
GROUP BY t.transfer_group, t.portfolio_code
ORDER BY min_trade_date, t.transfer_group;
```

**判读**：正常形态只有两种——`fund_legs=1, cash_legs=1`（买入组：基金
buy + CASH sell）或 `fund_legs=1, cash_legs IN (0,1)`（卖出组：无腿 = 未确认，
有腿 = 已确认）。其余组合都进 §3 的异常清单。

### 2.2 旧模型残留（本次处置的主要目标）

```sql
-- ① 旧买入组：扣款腿仍是 pending（新模型下恒为 confirmed）
SELECT t.id, t.transfer_group, t.portfolio_code, t.product_code, t.trade_type,
       t.status, t.trade_date, t.confirm_date, t.amount, t.actual_amount,
       t.platform_code
FROM trade t
WHERE t.transfer_group LIKE 'rebal\_%'
  AND t.product_code = 'CASH'
  AND t.trade_type  = 'sell'
  AND t.status      = 'pending'
ORDER BY t.trade_date, t.id;

-- ② 旧卖出组：到账腿是 pending（新模型下到账腿创建即 confirmed）
SELECT t.id, t.transfer_group, t.portfolio_code, t.product_code, t.trade_type,
       t.status, t.trade_date, t.confirm_date, t.amount, t.actual_amount,
       t.platform_code
FROM trade t
WHERE t.transfer_group LIKE 'rebal\_%'
  AND t.product_code = 'CASH'
  AND t.trade_type  = 'buy'
  AND t.status      = 'pending'
ORDER BY t.trade_date, t.id;

-- ③ 旧卖出组：到账腿的现金日锚在下单日而非基金确认日，或金额仍是创建期占位值
--    （新模型 trade_date 恒 = 基金腿 confirm_date、金额恒 = 确认后净额）
SELECT c.id AS cash_leg_id, f.id AS fund_leg_id, c.transfer_group,
       c.trade_date AS cash_trade_date, f.confirm_date AS fund_confirm_date,
       c.confirm_date AS cash_confirm_date, c.status AS cash_status,
       c.amount AS cash_amount, f.actual_amount AS fund_net_amount
FROM trade c
JOIN trade f
  ON f.transfer_group = c.transfer_group
 AND f.product_code  <> 'CASH'
WHERE c.transfer_group LIKE 'rebal\_%'
  AND c.product_code = 'CASH'
  AND c.trade_type   = 'buy'
  AND f.trade_type   = 'sell'
  AND (c.trade_date <> f.confirm_date
       OR c.amount   <> COALESCE(f.actual_amount, f.amount))
ORDER BY c.trade_date;

-- ④ 形态面（#581 补）：买入扣款腿的日期偏离新模型口径
--    新模型要求 CASH sell 腿 trade_date = confirm_date = T（#493 决策 5）
--    （`status='confirmed'` 使本查询与 ① 严格不相交；pending 腿由 ① 覆盖）
SELECT c.id, c.transfer_group, c.portfolio_code, c.platform_code, c.status,
       c.trade_date AS cash_trade_date, c.confirm_date AS cash_confirm_date, c.amount,
       f.id AS fund_leg_id, f.confirm_date AS fund_confirm_date
FROM trade c
JOIN trade f
  ON f.transfer_group = c.transfer_group
 AND f.product_code  <> 'CASH'
 AND f.trade_type     = 'buy'
WHERE c.transfer_group LIKE 'rebal\_%'
  AND c.product_code = 'CASH'
  AND c.trade_type   = 'sell'
  AND c.status       = 'confirmed'
  AND c.confirm_date <> c.trade_date
ORDER BY c.trade_date, c.id;

-- ④b 通用面（#581 补）：任何来源的 CASH 腿日期反转，含 sub_ 与转移组，一次看全
--    注意：`confirm_date` 可空且 `NULL < x` 在 MySQL 非 TRUE——NULL 行会静默逃过
--    本谓词；增量盘点如需覆盖 NULL 到账日，另加 OR confirm_date IS NULL
SELECT id, transfer_group, portfolio_code, platform_code, trade_type,
       status, trade_date, confirm_date, amount
FROM trade
WHERE product_code = 'CASH' AND confirm_date < trade_date
ORDER BY confirm_date, id;
```

> ③ 与 ② 有重叠是**预期**的：旧卖出组的到账腿既 `pending`、锚点也在下单日，
> 两条查询都会命中。按组去重后统一处置即可。
>
> **④/④b 与 ①②③ 正交，是本次补上的盘点盲区**（#581）：①② 只筛 `status='pending'`、
> ③ 只看卖出组的 `buy` 腿，**confirmed 的 CASH sell 腿的自有日期关系此前没有任何一条
> 查询覆盖**。`rebal\_%` 里的反斜杠是 MySQL 下 `_` 通配符的转义，不可去掉。
> 补盘结论见 §6.3。

### 2.3 结构性异常组

```sql
-- 孤儿/畸形组：基金腿数量 != 1、现金腿 > 1、或买入组缺扣款腿
SELECT t.transfer_group, t.portfolio_code,
       SUM(t.product_code <> 'CASH') AS fund_legs,
       SUM(t.product_code  = 'CASH') AS cash_legs,
       SUM(t.product_code <> 'CASH' AND t.trade_type = 'buy'
           AND t.status = 'confirmed') AS confirmed_buys,
       GROUP_CONCAT(CONCAT(t.id, ':', t.product_code, '/', t.trade_type, '/',
                           t.status, '/', t.trade_date)
                    ORDER BY t.id SEPARATOR ' | ') AS detail
FROM trade t
WHERE t.transfer_group LIKE 'rebal\_%'
GROUP BY t.transfer_group, t.portfolio_code
HAVING fund_legs <> 1 OR cash_legs > 1;

-- 买入组缺扣款腿（基金 buy 存在但组内无 CASH sell）
SELECT f.id, f.transfer_group, f.portfolio_code, f.status, f.trade_date,
       f.confirm_date, f.actual_amount
FROM trade f
WHERE f.transfer_group LIKE 'rebal\_%'
  AND f.product_code <> 'CASH'
  AND f.trade_type   = 'buy'
  AND NOT EXISTS (
      SELECT 1 FROM trade c
      WHERE c.transfer_group = f.transfer_group
        AND c.product_code   = 'CASH'
        AND c.trade_type     = 'sell'
  )
ORDER BY f.trade_date;
```

> 第二条同样会命中 §2.3 第一条已报出的畸形组（多基金腿组里也常缺 CASH 腿）。
> 以**组**为单位去重后处置，不要按行重复处理。

### 2.4 快照暴露面（决定处置顺序）

对 §2.2/§2.3 命中的每个组，取「组内最早会计生效日」，再查该日及之后是否已有快照——
新模型下 update/cancel/delete/unconfirm 都是**组级**快照保护，命中即须先删快照。

```sql
-- 把 :from_date 换成上方命中的 MIN(COALESCE(confirm_date, trade_date))
SELECT s.portfolio_code,
       COUNT(*)                 AS snapshots_from_date,
       MIN(s.snapshot_date)     AS first_snapshot_date,
       MAX(s.snapshot_date)     AS last_snapshot_date
FROM portfolio_value_snapshot s
WHERE s.portfolio_code = :portfolio_code
  AND s.snapshot_date >= :from_date
GROUP BY s.portfolio_code;

-- 受影响平台上的在途行（金额与平台归属须与手工处置结果对账）
SELECT p.portfolio_code, p.snapshot_date, p.product_code, p.platform_code,
       p.cash_amount
FROM portfolio_position p
WHERE p.product_code IN ('IN_TRANSIT_BUY', 'IN_TRANSIT_SELL')
ORDER BY p.portfolio_code, p.snapshot_date, p.product_code;
```

### 2.5 #471 污染清单

```sql
-- 被空确认的调仓基金腿：confirmed 但未取到价（#471 的 auto_confirm 既不取价、
-- 也不建腿，直接置 confirmed）。按方向分别判据——买入的份额是**算出来的**，
-- 零/缺即为空确认；卖出的份额是**录入量**（本就存在），只有缺价才说明确认时
-- 未取到净值。不加区分会把正常的卖出腿误报成污染。
SELECT t.id, t.portfolio_code, t.product_code, t.market, t.trade_type,
       t.status, t.trade_date, t.confirm_date,
       t.shares, t.price, t.amount, t.actual_amount, t.fee,
       t.transfer_group
FROM trade t
WHERE t.transfer_group LIKE 'rebal\_%'
  AND t.product_code <> 'CASH'
  AND t.status       =  'confirmed'
  AND (
        (t.trade_type = 'buy'
         AND (t.shares IS NULL OR t.shares = 0
              OR t.price IS NULL OR t.price = 0))
     OR (t.trade_type = 'sell'
         AND (t.price IS NULL OR t.price = 0))
  )
ORDER BY t.confirm_date, t.id;
```

配套核对：这些组的 CASH 腿是否已把现金扣掉/记入，以及对应快照里的持仓行。

---

## 3. 分类与处置决策

| 盘点结果 | 处置 |
| --- | --- |
| §2.2 ① 旧买入组（扣款腿 pending） | 按**实际交割事实**处理：扣款确实已发生 → 走新模型确认基金腿（`_apply_confirm_cash_leg` 会把扣款腿**状态与金额**校正为一致——判据 `_cash_leg_needs_fix` 只比 `status` 与 `actual_amount`，`corrected` 分支也只写 `status`/`amount`/`actual_amount`，**不写日期**；只有卖出到账腿的 `synced` 分支才重写 `trade_date`/`confirm_date`）；未发生 → 取消该组。**不得**只把 CASH 腿批量置 confirmed（见 §5 第 4 条），也不得指望确认路径把扣款腿日期带回 T |
| §2.2 ② 旧卖出组（到账腿 pending） | 取消确认基金腿后按新模型重新确认并录入真实到账日；或按实际事实确认为已到账 |
| §2.2 ③ 到账腿锚点/金额不符 | 同上：先满足快照保护，再由新规则重建到账腿 |
| §2.2 ④/④b confirmed 扣款腿日期偏离或反转 | 逐组人工判定（以对账单/交割单为据），**不得**机械批量改日期；受影响快照按组级保护先删后重算。补盘结论见 §6.3 |
| §2.3 畸形/孤儿组 | 逐组人工判定；缺扣款腿的买入组不得直接确认 |
| §2.5 #471 污染 | 按账核对 → 删受影响快照 → unconfirm 并**重新正确确认** → 重建快照并对账（仅重算快照无效） |
| 已完成的合法 confirmed 组 | **不动**：历史 `trade_date`/金额原样保留，读侧照实返回（不重写合法历史） |

---

## 4. 维护窗口（须明确授权）

1. 暂停一切会产生旧模型状态的写操作：调仓的**创建、确认、取消确认**（申赎与事件
   不受影响）。
2. 暂停相关自动任务：`daily_snapshot_generate`（快照推进）与 `snapshot_recalc_job`，
   避免处置中途又产生旧状态或半确认组。
3. 等待在途请求结束（确认无进行中的调仓写请求）。
4. **权限与调度变更须显式授权**，不由代码实现擅自修改。

---

## 5. 人工处置

1. 依 §3 逐组处置；每组处置前后各跑一次 §2.1 与 §2.4 的记录，形成前后对照。
2. **删快照前必须先备份**（见下方引用的两道保护），再进入下一次删除：
   - 备份已完成且**恢复演练过**——这条同时是 §6 的合并闸门项，但它的**执行时点在这里**，
     不能等闸门检查时才做：§5 的删除是真实破坏性动作，而 §6 只是事后核对。
   - 删除一律先走 `dry_run=true` 预览**级联范围**（删某日会按连续原则连带其后全部快照），
     把预览结果留痕后再以 `confirm=true` 执行（`routers/snapshots.py` 的批量删除端点
     即以此二参数为保护）。
3. 处置前核对「该组最早会计生效日」当天及之后是否有快照；有则**先删快照**
   （删除会按连续原则级联其后快照，须一并接受重算代价）。
4. **不得**把所有旧 CASH 腿批量置 confirmed 或批量删组——那会把「钱到底动没动」
   这一事实判断替换成机械操作，正是本次要消除的账实不符。
5. 每组处置完成后，用 §2.4 的在途行与平台可用现金做一次对账：
   `现金 + 在途 + 基金市值` 应与处置前一致（有手续费时差额即为该笔手续费，
   #493 的记账守恒口径见[现金账本规则](../reference/business-constraints.md#rule-cash)）。
6. 全程留痕：处置人、时间、组号、依据（对账单/交割单）、dry-run 预览结果、前后状态。

---

## 6. 合并闸门（逐项打勾后才能合并）

> 盘点与处置记录：**2026-09-18**，处置人 Collyn（`ADMIN`），写操作经生产 API（`ir` CLI →
> `https://investring.top`）执行，数据库侧仅用只读连接做备份与校验。
> 备份：`~/ir-backup-20260918/`（13 张表导出 + `manifest.json` 行数/字节清单）。
> 快照对齐口径：PORT005 追平至 **2026-09-14**，与处置前覆盖范围一致（PORT001 全程未动）。

- [x] §2.2 ① 查询返回 **0 行**（无旧买入组 pending 扣款腿）
- [x] §2.2 ② 查询返回 **0 行**（无旧卖出组 pending 到账腿）
- [x] §2.3 畸形/孤儿组查询返回 **0 行**（两条查询均 0 行；105 个 rebal 组 / 210 条腿**全部 confirmed**，无 pending、无 cancelled）
- [x] §2.5 #471 污染清单返回 **0 行**（原 5 行已按 §5 处置完毕，留痕见 §6.1；复检已清零）
- [ ] 代码/契约/测试已完成（后端全量 SQLite + MySQL、CLI 契约、前端 lint/tsc/build/E2E）
- [ ] 备份已完成且**恢复演练过**，对账结果已确认 —— 备份已完成并校验行数/字节；**恢复演练未做**（本机无 MySQL 实例，无法还原验证），此项未打勾
- [ ] 维护窗口仍在持续（防止检查通过后又产生旧状态），直到自动部署结束 —— 未停用定时任务：PORT001/PORT005 的 `auto_snapshot_enabled=0`，`snapshot_generate` 实际空转（`records_total=0`）；`nav_sync` 只写价格。窗口期内不得手工创建/确认调仓交易

### 6.1 §2.5 处置留痕（5 组 #471 空确认，PORT005 / 006282.OF / MGJJ）

处置方式：逐组「删快照 → unconfirm → `trade preview` 核对 → `trade confirm`（显式 `--confirm-date` 保持原 C，按 T 日净值取价）→ 追平快照 → 对账」。
依据：各组基金腿 confirmed 但 `shares=0.00, price=NULL`，配对 CASH 扣款腿已 confirmed（钱已扣、份额为零、确认日后也不再计入在途）。

| 组 | 基金腿 id | T → C | 依据净值（T 日） | 处置前 shares / price | 处置后 shares / price | 金额 |
| --- | --- | --- | --- | --- | --- | --- |
| `rebal_d9c6a0ad349d` | 427 | 09-08 → 09-10 | 1.8505 | 0.00 / NULL | 529.59 / 1.8505 | 980.00 |
| `rebal_c785b94ae07f` | 433 | 09-10 → 09-14 | 1.8166 | 0.00 / NULL | 539.47 / 1.8166 | 980.00 |
| `rebal_891301502049` | 435 | 09-11 → 09-15 | 1.8227 | 0.00 / NULL | 548.64 / 1.8227 | 1000.00 |
| `rebal_f5e785560f0f` | 447 | 09-14 → 09-16 | 1.8098 | 0.00 / NULL | 552.55 / 1.8098 | 1000.00 |
| `rebal_222a53a633cc` | 449 | 09-15 → 09-17 | 1.7982 | 0.00 / NULL | 556.11 / 1.7982 | 1000.00 |

- 恢复金额合计 **4960.00**；5 组双腿均已 confirmed，处置后全库 rebal pending 腿 = 0。
- **删快照**：`dry-run` 预览 5 张（09-14 → 09-08 倒序）→ 实删 5 张；级联回退事件 #21（现金分红 563020.SH，ex 09-11，`cash_change` 154.80，已被追平时的 auto_confirm 重新确认、数值不变）；申赎 53/54 因 `apply_date`=09-07 不在区间未回退、状态与数值未变。
- **对账**（现金 + 在途 + 基金市值；手续费为 0，差额为 006282.OF 净值波动）：

| 快照日 | 处置前 total_value | 处置后 total_value | 差额 |
| --- | --- | --- | --- |
| 09-08 | 899023.1249 | 899023.1249 | 0（该日在途，未受影响） |
| 09-09 | 897839.2259 | 897839.2259 | 0 |
| 09-10 | 892480.3429 | 893446.4209 | +966.0780 |
| 09-11 | 885196.6863 | 886158.7395 | +962.0532 |
| 09-14 | 884307.6214 | 886256.1970 | +1948.5756 |

- 在途金额逐日与处置前**完全一致**（09-14：QM 1000.00 + MGJJ 1000.00）；投资人份额逐日 908655.31 与备份一致；快照覆盖恢复为 PORT001 424 张、PORT005 21 张（08-17 ~ 09-14）。

> 未追平 09-15 ~ 09-17：`1001767344` / `1001767346`（HK_MUTUAL，`nav_sync` 长期失败的 2 只）自 09-14 起无净值，且 09-16 / 09-17 全市场缺价，追平会 `MISSING_NAV`。435/447/449 已正确确认，净值补齐后追平即自动入账，**不会再次蒸发**。

### 6.2 §2.2 ③ 书面结论（8 组，不在 §6 闸门列表内，未处置）

均系**卖出组**到账腿 `trade_date` 锚在下单日 T（新模型要求 = 基金确认日 C），但 `amount` 与 `fund_net_amount` **完全一致、无金额偏差**：

| 组 | 组合 | 现金腿 T | 基金腿 C | 到账日 A |
| --- | --- | --- | --- | --- |
| `rebal_7e73f0304c38` | PORT001 | 2026-06-30 | 2026-07-01 | 2026-07-03 |
| `rebal_8286272927e7` | PORT005 | 2026-08-19 | 2026-08-20 | 2026-08-20 |
| `rebal_db86ef39c236` | PORT005 | 2026-08-31 | 2026-09-02 | 2026-09-02 |
| `rebal_1ab7ea74fedb` | PORT005 | 2026-08-31 | 2026-09-02 | 2026-09-02 |
| `rebal_9d02e999829e` | PORT005 | 2026-08-31 | 2026-09-02 | 2026-09-02 |
| `rebal_f0c70317ec5d` | PORT005 | 2026-08-31 | 2026-09-02 | 2026-09-02 |
| `rebal_89f057c49e1a` | PORT005 | 2026-08-31 | 2026-09-02 | 2026-09-02 |
| `rebal_e2d02995a5f3` | PORT005 | 2026-09-03 | 2026-09-04 | 2026-09-04 |

**结论：按 §3 末行「已完成的合法 confirmed 组」不动。** 依据：现有读路径对 CASH 流入一律按 `confirm_date` 收口——可用现金流入（`Trade.confirm_date <= as_of`）、快照持仓增量（`confirm_date` 窗口）、卖出在途（到账腿 `confirm_date > D`）均**不读** CASH buy 的 `trade_date`；故只要 `confirm_date` 即真实到账日就不产生错账。强行校正 `trade_date` 需从组内最早生效日删快照，PORT001 那组将级联 55 张（06-30 起），代价远大于收益。
**待办**：人工核对这 8 组 `confirm_date` 是否与银行/基金对账单的到账日一致，一致则维持本结论。

### 6.3 §2.2 ④/④b 补盘结论（#581 盲区，1 组，不在 §6 闸门列表内，未处置）

①② 只筛 `status='pending'`、③ 只看卖出组的 `buy` 腿，**confirmed 的 CASH sell 腿（买入扣款腿）的自有日期关系此前无任何查询覆盖**。#493 前的 `attach_paired_cash_leg` 把基金腿 `confirm_date` 原样传播给扣款腿，而新模型要求 `trade_date = confirm_date = T`（决策 5）；若旧组只经 §3 首行的 `corrected` 分支处理（只写 `status`/`amount`/`actual_amount`，不写日期），该偏离会留在库里且 ①②③ 都看不见。

**取证方式（严格只读）**：不发 DB 连接、不调用任何 create/confirm/update/delete/unconfirm 端点。只读的**结构性保证**在前：`ir trade list` 经 `run_list`（纯 GET 分页）只到 `GET /api/trades`，命令链上无任何写路径（`ir-cli/ir_cli/commands/trades.py`、`utils.py`）。`ir trade list --product-code CASH --all --full` 逐组合取回后本地按 ④/④b 谓词比对；同命令连跑两次输出**逐字节一致**（各 87,465 bytes），作为两次读之间无第三方写入的自洽校验。对照面取 09-18 切换备份 `~/ir-backup-20260918/trade.json`（271 行 / 166 条 CASH 腿）以同一逻辑复算。取证执行日：**2026-09-20**。

| 查询 | 生产（09-20，171 条 CASH 腿） | 备份（09-18，166 条） |
| --- | --- | --- |
| ④b 通用面：任意 `product_code='CASH' AND confirm_date < trade_date` | **0 行** | **0 行** |
| ④ 形态面：`rebal_%` + CASH + sell + `confirm_date <> trade_date` | **1 行** | **1 行**（同一行） |

④b 为 0 意味着 #517 断言 5 的「存量日期反转行」确为空集，读侧不必为反转形态设计兜底。**严格前提**：`confirm_date` 可空且 MySQL 中 `NULL < x` / `NULL <> x` 均非 TRUE，④/④b 谓词会静默跳过 NULL 到账日的行——本次取证 171 条 CASH 腿的 `confirm_date` 均非空，故不存在 NULL 逃逸；日后增量盘点如需覆盖 NULL 到账日，另加 `OR confirm_date IS NULL`。另注：④ 此后已在 §2.2 收紧 `AND c.status = 'confirmed'`（与 ① 严格不相交）；本次取证执行时尚未加该过滤，唯一命中 id=98 为 confirmed，收紧不改变上表结果。

**唯一命中**：

| id | 组 | 组合 / 平台 | 方向 / 状态 | `trade_date` | `confirm_date` | 金额 | 同组基金腿 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 98 | `rebal_ef1e41850b04` | PORT001 / HXJJ | sell / confirmed | 2026-06-04 | **2026-06-05** | 4000.00 | 97（`022959.OF` buy / confirmed，`trade_date=06-04`、`confirm_date=06-05`、`actual_amount=4000.00`） |

**判定：真旧模型残留，非脏数据误报。** 扣款腿 `confirm_date` 恰等于同组基金腿的确认日 C，两腿金额一致——即「把基金 `confirm_date` 传播进 CASH 腿」这一 #493 明令禁止的形态，只有日期偏离，金额与交割事实无误。

**处置：不动**（§3 末行「已完成的合法 confirmed 组」同口径，理由与 §6.2 同构）。影响面按备份的 `portfolio_position`（3,180 行）实测如下：

- PORT001/HXJJ 的 CASH 行：06-03 `0.00` → **06-04 `4000.00`** → 06-05 `0.00`；且 06-04 当日 PORT001 **无 `IN_TRANSIT_BUY` 行**。该组合在 06-30 / 07-01 / 07-02 / 07-20 都产生过在途行，故 06-04 缺行不是「该组合不产生在途」。
- 4000 在两种模型下都只被计入一次，**组合总市值同值**；差异是 06-04 快照的**构成**——这笔钱记为现金而非记为在途，且该笔买入在下单日从未以在途形式留痕。
- 唯一可见的不一致在**历史时点查询**：`as_of = 2026-06-04` 时快照 CASH 行显示 4000.00，而可用现金按下单日锚定（`trade_date <= as_of`）已扣为 0。06-05 起两口径合流，**当期数值与后续所有快照日均不受影响**。
- 若校正该腿 `confirm_date` → 06-04，须从 2026-06-04 起删 PORT001 快照重算：备份中该日起共 **72 个快照日**（06-04 ~ 09-14），此后每新增一个快照日再加一张——代价高于 §6.2 中**最贵的单组**（PORT001，55 张，06-30 起），且换来的只是单日历史构成的一致。

> **若未来重算 PORT001 2026-06-04 及之后的快照**，该行的两口径差异（现金 vs 在途、06-04 的可用现金与现金持仓不一致）会随重算重新显现，届时按本节判定依据决定是否一并校正。重算路径按 `confirm_date` 窗口捕获现金增量，单独重算**不会**自动修正它。

**窗口覆盖**：备份 `manifest.created_at = 2026-09-18T01:43:37`，早于 #518 合并（`8dfdd7e`，2026-09-18T02:03:58+08:00）与随后的部署生效，中间这段是本节此前唯一的未取证区间。生产侧备份之后的 CASH 腿写入分两段：

- **窗口内（01:43:37 → 02:03:58）**：只有 §6.1 处置触到的 5 行（id 428 / 434 / 436 / 448 / 450，`updated_at` 01:44:56 ~ 01:45:27）。与备份逐字段比对，`trade_date` / `confirm_date` / `status` / `trade_type` / 金额 / `platform_code` **全部一致**（仅 `updated_at` 变动），且 5 行本已 `trade_date == confirm_date` → 未引入反转，也未留下旧模型日期。
- **窗口后**：新增 5 条腿（id 452 / 454 / 456 / 458 / 460，`created_at` 09-18 22:42:35 ~ 23:09:08）全部 `trade_date == confirm_date`，即新模型形态。

09-19 与 09-20 两天无任何 CASH 腿写入（`updated_at` 最大值仍为 09-18T23:09:08）→ 盲区关闭。

**防复发**：④/④b 已并入 §2.2 作为常规盘点项；「任一 CASH 腿恒有 `trade_date <= confirm_date`」作为读侧可依赖的**数据不变量**已写入[现金账本](../reference/business-constraints.md#rule-cash)（调仓侧日期闸门的两处实现见[调仓规则](../reference/business-constraints.md#rule-trade)）；结构性守卫（同一形态的自动检测）属 #538 范围，不在本 runbook。

---

## 7. 部署后验证与回退

1. 经授权用**明确的测试记录**（或小额真实业务记录）核对：现金、在途、份额、
   可用现金、人工确认路径（买入确认、卖出录入到账日、到账日修正）。
2. 记录一组新模型下单的完整链路（创建 → T 快照 → 确认 → C..A 在途 → 到账）
   作为上线后基线。
3. **新模型记录产生后不得仅回滚镜像**：旧代码会把半确认组（卖出组无 CASH 腿、
   买入组扣款腿 confirmed 而基金腿 pending）按旧不变量误处理。回退须另行确认
   数据处置或恢复方案，参照 `docs/runbooks/deploy-rollback.md`。
