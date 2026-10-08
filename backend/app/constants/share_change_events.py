"""份额变动事件的现金口径常量（单一事实来源）。

`event_type` 是自由字符串（`String(30)`，创建期无白名单——见
`share_change_event_service._compute_event_fields` 对未知类型「不计算、不写字段」的
处置），所以「哪些事件动现金」只能由白名单正向声明，不能按 `cash_change != 0` 反推：
未知类型的行同样可能带非零 `cash_change`。

引用方：`snapshot_service` 的现金腿、`cumulative_profit_service` 的现金腿。两处口径
必须一致，否则同一笔事件在快照现金与累计收益里被不同对待（#630 评审发现的缺陷：
读侧漏了白名单，未知类型的非零 `cash_change` 会同时抬高基金收益、压低现金收益）。
"""

# 只有这两类事件产生真实现金变动：
# - cash_dividend：现金分红，到账日由 `ShareChangeEvent.cash_effective_date` 决定
#   （有 cash_pay_date 用之，否则回落 ex_date，#522）；
# - forced_adjustment：shares_change / cash_change 由用户直填的强制调整（#263）。
# 其余类型（reinvest_dividend / share_split / share_merge / bonus_share）的 cash_change
# 恒为 0，只动份额。
CASH_EFFECT_EVENT_TYPES = ("cash_dividend", "forced_adjustment")

# 这四类事件增加（或减少）份额时**没有对应的外部资金进出**（#673）：再投资用的是
# 基金自己派发的红利，拆分/合并只改份额与净值、送股是白给。故持仓的成本基数
# （shares × cost_price）必须守恒，`cost_price` 随份额反向摊薄/浓缩——否则免成本份额
# 被按原成本价计入本金，holding_profit 系统性低估（2:1 拆分下 1000 份 @1.0 会凭空
# 产生 −1000 的假亏损）。
#
# **刻意不复用 `share_change_event_service.STRUCTURAL_SHARE_TYPES`**：两者集合当前恰好
# 相同，但语义无关——那个常量是「现金型产品无条件拒绝份额变动」（#279）的判据，
# 本常量是「份额变动不携带外部本金」的记账判据。合并成一个会让日后任一侧增删类型时
# 静默改变另一侧行为。
#
# `forced_adjustment` 刻意排除：shares_change 由用户直填，可能是数据纠错（不该动基数）
# 也可能是外部转入（该动但无客观成本价可用），无单一正确口径，维持既有行为。
#
# 引用方：`snapshot_service` 的事件应用段。
CAPITAL_FREE_SHARE_EVENT_TYPES = (
    "reinvest_dividend",
    "share_split",
    "share_merge",
    "bonus_share",
)
