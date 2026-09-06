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
}

export function DatePicker({
  id,
  date,
  onSelect,
  placeholder = "选择日期",
  className,
  disabled = false,
  showTradingDays = false,
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

  const hasCalendarData = showTradingDays && tradingDaySet.size > 0
  const modifiers = hasCalendarData
    ? {
        tradingDay: (day: Date) => tradingDaySet.has(toDateOnly(day)),
        // 仅对已加载年份的日期置灰，避免切换年份时新年数据未到位被误标非交易日
        nonTradingDay: (day: Date) =>
          loadedYears.has(day.getFullYear()) && !tradingDaySet.has(toDateOnly(day)),
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
          modifiers={modifiers}
          modifiersClassNames={modifiersClassNames}
          onSelect={(newDate) => {
            onSelect?.(newDate)
            if (newDate) {
              setOpen(false)
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
