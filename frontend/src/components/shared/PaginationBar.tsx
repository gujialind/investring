"use client";

import { Button } from "@/components/ui/button";
import {
  Pagination,
  PaginationContent,
  PaginationEllipsis,
  PaginationItem,
  PaginationLink,
  PaginationNext,
  PaginationPrevious,
  buildPageItems,
} from "@/components/ui/pagination";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

const PAGE_SIZE_OPTIONS = [20, 50, 100];

interface PaginationBarProps {
  page: number;
  pageSize: number;
  total: number;
  /**
   * 后端折算好的总页数（#752）：按组分页时一页可能多带同组剩余腿，总页数不再等于
   * `ceil(total / pageSize)`，此时必须由调用方传入。不传则按行口径自行折算。
   */
  totalPages?: number;
  variant: "desktop" | "mobile";
  onPageChange: (page: number) => void;
  onPageSizeChange?: (pageSize: number) => void;
}

/**
 * 业务分页条（规范 §11）：桌面 = 共 N 条 + 页码原语 + 每页条数切换；
 * 移动 = 上一页 / 第 x / N 页 / 下一页。整行右对齐；只有一页时不渲染。
 */
export default function PaginationBar({
  page,
  pageSize,
  total,
  totalPages,
  variant,
  onPageChange,
  onPageSizeChange,
}: PaginationBarProps) {
  const pages = totalPages ?? Math.max(1, Math.ceil(total / pageSize));
  if (pages <= 1) return null;

  if (variant === "mobile") {
    return (
      <div className="flex items-center justify-end gap-3 pt-3">
        <Button
          variant="outline"
          size="sm"
          disabled={page <= 1}
          onClick={() => onPageChange(page - 1)}
        >
          上一页
        </Button>
        <span className="text-xs text-muted-foreground">
          第 {page} / {pages} 页
        </span>
        <Button
          variant="outline"
          size="sm"
          disabled={page >= pages}
          onClick={() => onPageChange(page + 1)}
        >
          下一页
        </Button>
      </div>
    );
  }

  return (
    <div className="flex items-center justify-end gap-3 pt-3">
      <span className="text-xs text-muted-foreground">共 {total} 条</span>
      <Pagination className="mx-0 w-auto justify-end">
        <PaginationContent>
          <PaginationItem>
            <PaginationPrevious
              disabled={page <= 1}
              onClick={() => onPageChange(page - 1)}
            />
          </PaginationItem>
          {buildPageItems(page, pages).map((item, idx) => (
            <PaginationItem key={item === "ellipsis" ? `ellipsis-${idx}` : item}>
              {item === "ellipsis" ? (
                <PaginationEllipsis />
              ) : (
                <PaginationLink
                  isActive={item === page}
                  onClick={() => onPageChange(item)}
                >
                  {item}
                </PaginationLink>
              )}
            </PaginationItem>
          ))}
          <PaginationItem>
            <PaginationNext
              disabled={page >= pages}
              onClick={() => onPageChange(page + 1)}
            />
          </PaginationItem>
        </PaginationContent>
      </Pagination>
      {onPageSizeChange && (
        <Select
          value={String(pageSize)}
          onValueChange={(v) => onPageSizeChange(Number(v))}
        >
          <SelectTrigger className="h-9 w-28">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {PAGE_SIZE_OPTIONS.map((size) => (
              <SelectItem key={size} value={String(size)}>
                {size} 条/页
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      )}
    </div>
  );
}
