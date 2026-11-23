import { useEffect, useState } from "react";
import { CaretLeftIcon, CaretRightIcon } from "@phosphor-icons/react";

export const EVAL_PAGE_SIZE = 50;

/** Pages a list in the browser. The page resets whenever resetKey changes (a new filter or sort). */
export function usePaged<T>(items: T[], resetKey: string, size = EVAL_PAGE_SIZE) {
  const [page, setPage] = useState(1);
  useEffect(() => {
    setPage(1);
  }, [resetKey]);
  const totalPages = Math.max(1, Math.ceil(items.length / size));
  const current = Math.min(page, totalPages);
  return {
    rows: items.slice((current - 1) * size, current * size),
    page: current,
    totalPages,
    total: items.length,
    setPage,
  };
}

export function Pager({
  page,
  totalPages,
  total,
  setPage,
}: {
  page: number;
  totalPages: number;
  total: number;
  setPage: (page: number) => void;
}) {
  if (totalPages <= 1) return null;
  return (
    <div className="pagination">
      <button className="btn btn-small" disabled={page <= 1} onClick={() => setPage(page - 1)}>
        <CaretLeftIcon size={12} weight="bold" /> Prev
      </button>
      <span className="pagination-info">
        Page {page} of {totalPages} · {total} episodes
      </span>
      <button className="btn btn-small" disabled={page >= totalPages} onClick={() => setPage(page + 1)}>
        Next <CaretRightIcon size={12} weight="bold" />
      </button>
    </div>
  );
}
