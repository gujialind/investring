"use client"

import * as React from "react"
import { format } from "date-fns"
import { CalendarIcon, X } from "lucide-react"

import { cn, toDateOnly } from "@/lib/utils"
import { Button } from "@/components/ui/button"
import { Calendar } from "@/components/ui/calendar"
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover"
import { useTradingCalendar } from "@/hooks/useTradingCalendar"

interface DatePickerProps {
  /** 透传到触发按钮，供配套 Label 的 htmlFor 关联（a11y / getByLabel 定位） */
  id?: string
  date?: Date
  onSelect?: (date: Date | undefined) => void
  placeholder?: string
  className?: string
  disabled?: boolean
  /** 标注交易日（绿色圆点），数据来自后端 trading-calendar，未加载时不标注 */
  showTradingDays?: boolean
  /**
   * 逐日不可选谓词（#493 加固）：**一旦提供**日历进入硬禁用模式——谓词命中的日期与
   * 已加载年份内的非交易日都不可点选（不只是置灰），从而把未来的后端 422 提前到选择时。
   * 不提供时保持原行为（非交易日仅置灰、可点选）。
   * 与 `disabled` 的区别：`disabled` 禁用整个控件，本谓词只禁用部分日期。
   */
  dayDisabled?: (day: Date) => boolean
}

export function DatePicker({
  id,
  date,
  onSelect,
  placeholder = "选择日期",
  className,
  disabled = false,
  showTradingDays = false,
  dayDisabled,
}: DatePickerProps) {
  const [open, setOpen] = React.useState(false)
  // 跟踪日历当前展示的月份，切换年份时按年拉取交易日历
  const [month, setMonth] = React.useState<Date>(() => date ?? new Date())

  React.useEffect(() => {
    if (open) setMonth(date ?? new Date())
  }, [open, date])

  const { data: calendarDays } = useTradingCalendar(
    month.getFullYear(),
    showTradingDays && open
  )

  const { tradingDaySet, loadedYears } = React.useMemo(() => {
    const daySet = new Set<string>()
    const yearSet = new Set<number>()
    calendarDays?.forEach((d) => {
      if (d.is_open) daySet.add(d.calendar_date)
      yearSet.add(Number(d.calendar_date.slice(0, 4)))
    })
    return { tradingDaySet: daySet, loadedYears: yearSet }
  }, [calendarDays])

  // 「已加载年份内的非交易日」判据（置灰与硬禁用共用一份，避免两处漂移成
  // 「置灰却可点」或「不可点却无灰」）：只判已加载年份，避免切换年份时
  // 新年数据未到位被误标/误禁
  const isNonTradingDay = React.useCallback(
    (day: Date) => loadedYears.has(day.getFullYear()) && !tradingDaySet.has(toDateOnly(day)),
    [loadedYears, tradingDaySet]
  )

  const hasCalendarData = showTradingDays && tradingDaySet.size > 0
  const modifiers = hasCalendarData
    ? {
        tradingDay: (day: Date) => tradingDaySet.has(toDateOnly(day)),
        nonTradingDay: isNonTradingDay,
      }
    : undefined
  const modifiersClassNames = hasCalendarData
    ? {
        // 交易日：日期下方小圆点（day 单元格自带 relative 定位；success 色 = 可用/正常态）
        tradingDay:
          "after:absolute after:bottom-0.5 after:left-1/2 after:-translate-x-1/2 after:h-1 after:w-1 after:rounded-full after:bg-success after:pointer-events-none",
        nonTradingDay: "text-muted-foreground/60",
      }
    : undefined

  // 硬禁用（#493 加固）：仅在调用方给了 dayDisabled 时启用，故既有调用点行为不变。
  // 非交易日判据复用上方 `isNonTradingDay`（与置灰同源，不再逐字复制一份）。
  const disabledDays = React.useMemo(() => {
    if (!dayDisabled) return undefined
    return (day: Date) => isNonTradingDay(day) || dayDisabled(day)
  }, [dayDisabled, isNonTradingDay])

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <div className="relative">
        <PopoverTrigger asChild>
          <Button
            id={id}
            type="button"
            variant="outline"
            disabled={disabled}
            className={cn(
              "w-full justify-start text-left font-normal pr-9",
              !date && "text-muted-foreground",
              className
            )}
          >
            <CalendarIcon className="mr-2 h-4 w-4 shrink-0" />
            {date ? format(date, "yyyy-MM-dd") : placeholder}
          </Button>
        </PopoverTrigger>
        {date && !disabled ? (
          <button
            type="button"
            aria-label="清除日期"
            onClick={(e) => {
              e.stopPropagation()
              onSelect?.(undefined)
            }}
            className="absolute right-2 top-1/2 flex h-5 w-5 -translate-y-1/2 items-center justify-center rounded-sm text-muted-foreground transition-colors hover:text-foreground"
          >
            <X className="h-3.5 w-3.5" />
          </button>
        ) : null}
      </div>
      <PopoverContent
        align="start"
        className="w-auto p-0"
        onCloseAutoFocus={(e) => e.preventDefault()}
      >
        <Calendar
          mode="single"
          selected={date}
          month={month}
          onMonthChange={setMonth}
          disabled={disabledDays}
          modifiers={modifiers}
          modifiersClassNames={modifiersClassNames}
          onSelect={(newDate) => {
            // #542：v10 点击已选日期会 toggle-off 传 undefined——点任何日都关弹层，
            // 且 toggle-off 不回传调用方（清空唯一入口是上方 X 按钮），否则字段被无声抹掉
            setOpen(false)
            if (newDate) {
              onSelect?.(newDate)
            }
          }}
          autoFocus
        />
        {hasCalendarData && (
          <div className="flex items-center gap-1.5 border-t px-3 py-2 text-xs text-muted-foreground">
            <span className="h-1.5 w-1.5 rounded-full bg-success" />
            交易日
          </div>
        )}
      </PopoverContent>
    </Popover>
  )
}
