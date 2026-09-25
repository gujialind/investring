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
