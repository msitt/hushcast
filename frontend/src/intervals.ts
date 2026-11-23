/** A half-open time range in seconds. */
export type Iv = [number, number];

/** Sorted, with overlapping and touching ranges merged. Empty ranges are dropped. */
export function union(a: Iv[]): Iv[] {
  const out: Iv[] = [];
  for (const [s, e] of [...a].filter((i) => i[1] > i[0]).sort((p, q) => p[0] - q[0])) {
    const last = out[out.length - 1];
    if (last && s <= last[1]) last[1] = Math.max(last[1], e);
    else out.push([s, e]);
  }
  return out;
}

/** The parts of a that b does not cover. */
export function subtract(a: Iv[], b: Iv[]): Iv[] {
  const out: Iv[] = [];
  const cut = union(b);
  for (const [s, e] of union(a)) {
    let cursor = s;
    for (const [bs, be] of cut) {
      if (be <= cursor || bs >= e) continue;
      if (bs > cursor) out.push([cursor, bs]);
      cursor = Math.max(cursor, be);
      if (cursor >= e) break;
    }
    if (cursor < e) out.push([cursor, e]);
  }
  return out;
}
