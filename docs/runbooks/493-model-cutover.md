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
  凭空少一笔。这正是新代码在编辑路径上专门修掉的形态（见根 `AGENTS.md` §2.5）。
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
```

> ③ 与 ② 有重叠是**预期**的：旧卖出组的到账腿既 `pending`、锚点也在下单日，
> 两条查询都会命中。按组去重后统一处置即可。

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
| §2.2 ① 旧买入组（扣款腿 pending） | 按**实际交割事实**处理：扣款确实已发生 → 走新模型确认基金腿（代码会把扣款腿校正为 confirmed）；未发生 → 取消该组。**不得**只把 CASH 腿批量置 confirmed（见 §5 第 4 条） |
| §2.2 ② 旧卖出组（到账腿 pending） | 取消确认基金腿后按新模型重新确认并录入真实到账日；或按实际事实确认为已到账 |
| §2.2 ③ 到账腿锚点/金额不符 | 同上：先满足快照保护，再由新规则重建到账腿 |
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
   #493 的记账守恒口径见根 `AGENTS.md` §2.5）。
6. 全程留痕：处置人、时间、组号、依据（对账单/交割单）、dry-run 预览结果、前后状态。

---

## 6. 合并闸门（逐项打勾后才能合并）

- [ ] §2.2 ① 查询返回 **0 行**（无旧买入组 pending 扣款腿）
- [ ] §2.2 ② 查询返回 **0 行**（无旧卖出组 pending 到账腿）
- [ ] §2.3 畸形/孤儿组查询返回 **0 行**，或每一行都有书面处置结论
- [ ] §2.5 #471 污染清单返回 **0 行**，或每一行都有书面处置结论
- [ ] 代码/契约/测试已完成（后端全量 SQLite + MySQL、CLI 契约、前端 lint/tsc/build/E2E）
- [ ] 备份已完成且**恢复演练过**，对账结果已确认
- [ ] 维护窗口仍在持续（防止检查通过后又产生旧状态），直到自动部署结束

---

## 7. 部署后验证与回退

1. 经授权用**明确的测试记录**（或小额真实业务记录）核对：现金、在途、份额、
   可用现金、人工确认路径（买入确认、卖出录入到账日、到账日修正）。
2. 记录一组新模型下单的完整链路（创建 → T 快照 → 确认 → C..A 在途 → 到账）
   作为上线后基线。
3. **新模型记录产生后不得仅回滚镜像**：旧代码会把半确认组（卖出组无 CASH 腿、
   买入组扣款腿 confirmed 而基金腿 pending）按旧不变量误处理。回退须另行确认
   数据处置或恢复方案，参照 `docs/runbooks/deploy-rollback.md`。
