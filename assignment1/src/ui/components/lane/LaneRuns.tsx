'use client';
import { useVirtualizer } from '@tanstack/react-virtual';
import { ArrowDown } from 'lucide-react';
import {
  type KeyboardEvent,
  type MouseEvent,
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type WheelEvent,
} from 'react';
import { BlockRow } from '@/ui/components/blocks/BlockRow';
import { useLaneRuns, useMissionStore } from '@/ui/store/missionStore';
import { callElementId, callKey, callPath, flattenRows } from '@/ui/store/rows';
import { PromptBubble } from './PromptBubble';
import { RunFooter } from './RunFooter';

export function LaneRuns({ laneId }: { laneId: string }) {
  const runs = useLaneRuns(laneId);
  const rows = useMemo(() => flattenRows(runs), [runs]);
  const parentRef = useRef<HTMLDivElement>(null);
  // Where this lane was left last time it was mounted. Reading it during the initial state means
  // the first paint already knows whether to follow the tail, so a reader who had scrolled up is
  // not yanked to the bottom for a frame before being put back.
  const restore = useRef(useMissionStore.getState().laneScrollByLane[laneId]);
  const [following, setFollowing] = useState(restore.current?.following ?? true);
  const runsById = useMemo(() => Object.fromEntries(runs.map((r) => [r.runId, r])), [runs]);
  const lastScrollTop = useRef(0);
  // Our own follow scrolls (and the virtualizer's size corrections right after them) can move the
  // scroll position in either direction for a frame or two; only an upward move outside that window
  // counts as the user scrolling away.
  const followScrollUntil = useRef(0);
  const runCount = useRef(runs.length);
  // The cleanup above closes over the mount-time `following`; this is what it actually reads.
  const followingRef = useRef(following);
  followingRef.current = following;
  // A drag on the container's own scrollbar (or a touch drag on the list) fires plain `scroll`
  // events indistinguishable from a programmatic one — track the gesture explicitly so an upward
  // move during it unfollows even while a stream of events is pushing `followScrollUntil` forward.
  const userScrolling = useRef(false);

  const virtualizer = useVirtualizer({
    count: rows.length,
    getScrollElement: () => parentRef.current,
    // A folded tool card / text block / thinking row typically measures 150-300px, not the ~72px a
    // one-line row would suggest — an estimate that low made the virtualizer believe far more rows
    // fit on screen than actually do, so the real visible range (post-measurement) undershot what
    // `overscan` was calculated to cover and only a handful of rows stayed mounted. Estimating closer
    // to the real average keeps the pre-measurement layout close enough that a fixed, modest overscan
    // is enough on both counts: everything mounts for a small trajectory (up to ~80 rows, which covers
    // every fixture here), and only a windowed slice (~85 rows for a 2,000-event run) mounts for a very
    // long one. With auto-follow pinned to the bottom, overscan only extends *above* the visible range
    // (nothing is below it), so it must cover a whole trajectory's row count on its own, not half of it.
    estimateSize: () => 160,
    overscan: 40,
    getItemKey: (i) => rows[i]?.key ?? i,
  });

  const onScroll = useCallback(() => {
    const el = parentRef.current;
    if (!el) return;
    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 48;
    const movedUp = el.scrollTop < lastScrollTop.current - 1;
    lastScrollTop.current = el.scrollTop;
    if (atBottom) setFollowing(true);
    // A user-driven drag (scrollbar or touch) is unambiguous, so an upward move during one unfollows
    // regardless of the follow-scroll window; otherwise the window still guards against our own
    // programmatic scrolls and the virtualizer's post-scroll size corrections.
    else if (movedUp && (userScrolling.current || performance.now() > followScrollUntil.current))
      setFollowing(false);
  }, []);

  // A wheel or key press upwards is unambiguous user intent, whatever the scroll position does next
  // — unless it lands inside a card's own scroller (a long result, a diff, a notice) that can still
  // move in that direction, in which case the wheel belongs to that box and the lane keeps following.
  const onWheel = useCallback((e: WheelEvent<HTMLDivElement>) => {
    if (e.deltaY >= 0) return;
    const inner = (e.target as HTMLElement | null)?.closest?.('.result-text, .hunk, .notice-text');
    if (inner && inner.scrollTop > 0) return;
    setFollowing(false);
  }, []);
  const onKeyDown = useCallback((e: KeyboardEvent<HTMLDivElement>) => {
    if (e.key === 'ArrowUp' || e.key === 'PageUp' || e.key === 'Home') setFollowing(false);
  }, []);
  // A mousedown that lands on the container's own box (not on any row inside it) is a hit on the
  // scrollbar or the container's padding, not on content — the only way to "click" the container
  // itself. Track the drag until mouseup anywhere, since the pointer commonly leaves the scrollbar's
  // thin hit area mid-drag.
  const onMouseDown = useCallback((e: MouseEvent<HTMLDivElement>) => {
    if (e.target !== e.currentTarget) return;
    userScrolling.current = true;
    const onMouseUp = () => {
      userScrolling.current = false;
      window.removeEventListener('mouseup', onMouseUp);
    };
    window.addEventListener('mouseup', onMouseUp);
  }, []);
  const onTouchStart = useCallback(() => {
    userScrolling.current = true;
  }, []);
  const onTouchEnd = useCallback(() => {
    userScrolling.current = false;
  }, []);
  // A drag that ends outside the window — the pointer leaves the browser, the user switches app
  // mid-drag — never delivers its `mouseup`/`touchend`, which would leave the lane believing a
  // gesture is still in progress and unfollowing on the next size correction. Losing focus ends it.
  useEffect(() => {
    const onBlur = () => {
      userScrolling.current = false;
    };
    window.addEventListener('blur', onBlur);
    return () => window.removeEventListener('blur', onBlur);
  }, []);

  /**
   * Put the reader back where they were, and remember where they end up.
   *
   * This component is unmounted whenever the session view shows something else — a delegate's
   * trajectory, the outputs — because two trajectories mounted at once would duplicate the tool
   * cards' DOM ids. Without this, coming back from a child always landed at the bottom, following,
   * which is precisely the wrong place for somebody who had scrolled up to read the message that
   * spawned that child.
   *
   * **Saving cannot read the DOM.** The first version did `parentRef.current.scrollTop` in the
   * cleanup and silently saved nothing: React detaches refs before flushing passive cleanups, so
   * `parentRef.current` is already `null` there — and capturing the element at setup instead only
   * moves the problem, because by then the node is detached and a detached element reports
   * `scrollTop: 0`. The position is therefore mirrored into a ref as the lane scrolls
   * (`lastScrollTop`, which `onScroll` already maintains) and *that* is what is saved. It is
   * ordinary JavaScript state, so no teardown ordering can take it away.
   *
   * The restore does touch the DOM, but on mount, when the element certainly exists. It runs in a
   * layout effect so the corrected position is in place before the browser paints — a rAF here
   * showed the tail for one frame first — and is repeated once the virtualizer has measured, since
   * a fresh mount's estimated total height can be shorter than the saved offset. It only runs when
   * the lane was *not* following: one left at the tail belongs at the tail, which is where a fresh
   * mount already is.
   */
  useLayoutEffect(() => {
    const saved = restore.current;
    const el = parentRef.current;
    if (saved && !saved.following && el) {
      // Suppress the unfollow that this very scroll would otherwise look like, and keep the
      // mirrored position honest in case nothing scrolls again before the next unmount.
      const place = () => {
        followScrollUntil.current = performance.now() + 300;
        el.scrollTop = Math.min(saved.offset, Math.max(0, el.scrollHeight - el.clientHeight));
        lastScrollTop.current = el.scrollTop;
      };
      place();
      // Again after measurement: the virtualizer estimates row heights on the first pass, so the
      // scroll height it can accept grows a frame or two later.
      requestAnimationFrame(place);
      const settle = setTimeout(place, 120);
      return () => clearTimeout(settle);
    }
    return undefined;
  }, []);

  // Saved from the mirrored position, never from the element (see above). Split from the restore so
  // that this runs exactly once, on unmount, whatever re-renders happen in between.
  useEffect(
    () => () => {
      useMissionStore.getState().rememberLaneScroll(laneId, {
        offset: lastScrollTop.current,
        following: followingRef.current,
      });
    },
    [laneId],
  );

  // A new run in this lane — a submitted prompt, a replay, an import — is something somebody just
  // asked for, so the lane follows it even when the user had scrolled up to read an earlier run.
  useEffect(() => {
    if (runs.length > runCount.current) setFollowing(true);
    runCount.current = runs.length;
  }, [runs.length]);

  // biome-ignore lint/correctness/useExhaustiveDependencies: runs is intentional — a run's blocks can grow taller in place (e.g. a pending call's result arriving) without changing rows.length, and we still want to re-pin to the bottom when that happens.
  useEffect(() => {
    if (following && rows.length > 0) {
      followScrollUntil.current = performance.now() + 300;
      virtualizer.scrollToIndex(rows.length - 1, { align: 'end' });
    }
  }, [rows.length, following, virtualizer, runs]);

  // The outline asks for a jump through the store rather than reaching for `getElementById`, because
  // the card it points at is very often not in the document: virtualized out of the mounted window,
  // or folded away inside a subagent group. Scrolling the *row* is the virtualizer's job, so it
  // happens here; unfolding the groups on the way down is a request the groups pick up themselves,
  // including the ones that only mount once this scroll brings their row into the window.
  const jumpRequest = useMissionStore((s) => s.jumpRequest);
  const servedJump = useRef(0);
  useEffect(() => {
    if (!jumpRequest || jumpRequest.laneId !== laneId || jumpRequest.nonce === servedJump.current) return;
    servedJump.current = jumpRequest.nonce;
    const target = jumpRequest.call;
    useMissionStore.getState().clearJump(jumpRequest.nonce);
    const path = callPath(runs, target);
    if (!path) return;
    // Ancestors belong to the same run as the target, so they are addressed the same way: a replay
    // of the same recording has groups with identical call ids that must stay folded.
    const ancestors = path.slice(0, -1).map((callId) => ({ runId: target.runId, callId }));
    for (const ancestor of ancestors) useMissionStore.getState().openGroupFor(ancestor);
    const index = rows.findIndex(
      (r) =>
        r.kind === 'block' &&
        r.runId === target.runId &&
        r.block.kind === 'tool' &&
        r.block.callId === path[0],
    );
    if (index < 0) return;
    setFollowing(false);
    virtualizer.scrollToIndex(index, { align: 'center' });
    // `scrollToIndex` lands on the row's estimated position and mounts it; the card itself only
    // exists (and only has its real height, and its group only unfolds) a commit later. Re-centre on
    // the card once it is there — twice, because a tall row's measurement can correct the offset
    // again after the first frame. Both are idempotent, and a null lookup is simply a no-op.
    const centre = () => document.getElementById(callElementId(target))?.scrollIntoView({ block: 'center' });
    requestAnimationFrame(centre);
    setTimeout(centre, 120);
    // Once the groups on the path have had time to mount and unfold, drop the open requests so a
    // group that later remounts (virtualized out and back) returns to its own fold state instead
    // of being forced open forever by a jump the user made minutes ago.
    setTimeout(() => useMissionStore.getState().settleGroupOpen(ancestors), 600);
  }, [jumpRequest, laneId, rows, runs, virtualizer]);

  const items = virtualizer.getVirtualItems();

  // Track which top-level tool call is nearest the top of the viewport so the outline can highlight
  // it. Rows are virtualized, so the set of mounted `[data-depth="0"]` cards changes as the list
  // scrolls — re-observe whenever that mounted set could have changed (neither `items.length`,
  // `items[0]?.index`, nor `rows.length` is read inside the effect; all three are re-run triggers).
  // An IntersectionObserver callback only receives entries whose intersection state *changed* since
  // the last callback, not every observed element — so the previous "sort this batch, take the
  // first" approach could pick a stale/wrong element once a batch stopped including the true topmost
  // card. Track intersection state per element across callbacks instead, and re-derive the topmost
  // currently-intersecting element (by live `getBoundingClientRect().top`) on every callback.
  // biome-ignore lint/correctness/useExhaustiveDependencies: items.length, items[0]?.index, and rows.length are deliberately-extra re-run triggers, not values the effect reads.
  useEffect(() => {
    const el = parentRef.current;
    if (!el) return;
    const intersecting = new Map<Element, boolean>();
    let activeKey: string | null = null;
    const io = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) intersecting.set(entry.target, entry.isIntersecting);
        let top: Element | null = null;
        let topY = Number.POSITIVE_INFINITY;
        for (const [target, isIn] of intersecting) {
          if (!isIn) continue;
          const y = target.getBoundingClientRect().top;
          if (y < topY) {
            topY = y;
            top = target;
          }
        }
        const callId = top?.getAttribute('data-call-id');
        const runId = top?.getAttribute('data-run-id');
        if (!callId || !runId) return;
        const ref = { runId, callId };
        if (callKey(ref) === activeKey) return;
        activeKey = callKey(ref);
        useMissionStore.getState().setActiveCall(laneId, ref);
      },
      { root: el, rootMargin: '0px 0px -60% 0px', threshold: 0 },
    );
    for (const card of el.querySelectorAll('[data-testid="tool-card"][data-depth="0"]')) io.observe(card);
    return () => io.disconnect();
  }, [laneId, items.length, items[0]?.index, rows.length]);

  if (runs.length === 0) {
    return (
      <div className="lane-runs" data-testid="lane-runs">
        <div className="lane-empty faint" data-testid="lane-empty">
          Nothing has run here yet.
        </div>
      </div>
    );
  }

  return (
    <section
      className="lane-runs"
      // A named section, so the trajectory is a place a screen reader can be sent to, and focusable
      // so ArrowUp/PageUp scroll it without clicking a card first.
      // biome-ignore lint/a11y/noNoninteractiveTabindex: a scroll container has to be focusable or the keyboard cannot move it.
      tabIndex={0}
      aria-label="Trajectory"
      ref={parentRef}
      onScroll={onScroll}
      onWheel={onWheel}
      onKeyDown={onKeyDown}
      onMouseDown={onMouseDown}
      onTouchStart={onTouchStart}
      onTouchEnd={onTouchEnd}
      onTouchCancel={onTouchEnd}
      data-testid="lane-runs"
    >
      <div style={{ height: virtualizer.getTotalSize(), position: 'relative' }}>
        {/*
          The mounted rows sit in normal flow inside one wrapper translated to the first row's offset,
          rather than each row being placed at its own `item.start`. A row's real height only reaches
          the virtualizer after it renders (ResizeObserver, then an asynchronous re-render), and while
          a run streams a mounted row grows in place on every event — a pending Agent card gaining a
          nested call, a result arriving. Positioned individually, the row after it (the RunFooter,
          typically) kept its previous offset for the frame or more that the measurement took and was
          painted over the new content. In flow, growth pushes the neighbours down in the same layout
          pass, so no two rows can ever overlap; the measurement only feeds the total height and this
          wrapper's offset, both of which can lag harmlessly.
        */}
        <div
          style={{
            position: 'absolute',
            top: 0,
            left: 0,
            width: '100%',
            transform: `translateY(${items[0]?.start ?? 0}px)`,
          }}
        >
          {items.map((item) => {
            const row = rows[item.index];
            const run = row ? runsById[row.runId] : undefined;
            if (!row || !run) return null;
            return (
              // `flow-root` keeps a row's inner margins (the footer's top margin, markdown paragraph
              // margins) inside its own border box, which is what `measureElement` measures — an
              // absolutely positioned box did that implicitly; a plain block would let them collapse
              // out and every offset below would drift by the collapsed amount.
              //
              // The `.row-block` entrance animation (see components.css) animates `transform`; it stays
              // on the inner `.row`, never on an element the virtualizer positions with a transform of
              // its own, because once the animation's fill-mode holds at its final `transform: none`
              // keyframe it wins the cascade over an inline style.
              <div
                key={item.key}
                data-index={item.index}
                ref={virtualizer.measureElement}
                style={{ display: 'flow-root' }}
              >
                <div className={`row row-${row.kind}`} data-live={run.status === 'running'}>
                  {row.kind === 'prompt' && <PromptBubble run={run} />}
                  {row.kind === 'block' && <BlockRow block={row.block} run={run} />}
                  {row.kind === 'footer' && <RunFooter run={run} />}
                </div>
              </div>
            );
          })}
        </div>
      </div>
      {!following && rows.length > 0 && (
        <button
          type="button"
          className="jump-latest"
          onClick={() => {
            setFollowing(true);
            virtualizer.scrollToIndex(rows.length - 1, { align: 'end' });
          }}
          data-testid="jump-latest"
        >
          <ArrowDown size={12} /> Jump to latest
        </button>
      )}
    </section>
  );
}
