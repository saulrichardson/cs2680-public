import type { CSSProperties } from 'react';
import type { LaneDto } from '@/core/types';

/** How many identity hues there are before they repeat (see `--lane-hue-*` in tokens.css). */
const LANE_HUE_COUNT = 6;

/**
 * The hue that identifies one lane. Six is enough for the board's own limit (a fan-out races at
 * most six agents) and a seventh lane repeating the first is better than inventing a hue nobody can
 * tell from the sixth.
 */
function laneHue(index: number): string {
  const n = ((index % LANE_HUE_COUNT) + LANE_HUE_COUNT) % LANE_HUE_COUNT;
  return `var(--lane-hue-${n + 1})`;
}

/**
 * Which hue a lane wears, from the lane itself rather than from where it happens to sit.
 *
 * A racing lane is identified by its place in the race — "Agent 2" is the second worktree, on both
 * the board and in Compare. Any other lane is identified by its position on the board. Taking the
 * board position for everything meant an independent lane sitting above a race shifted every racer's
 * colour on the board while Compare kept the race's own numbering, so the same agent changed colour
 * between the two views (readiness review).
 *
 * What this guarantees is that one agent wears one colour everywhere, not that two lanes on screen
 * always differ: a racer and an independent lane can land on the same hue, as can the first and
 * seventh of anything (see `laneHue`).
 */
export function laneHueIndex(lane: Pick<LaneDto, 'groupId' | 'groupIndex'>, boardPosition: number): number {
  return lane.groupId !== null && lane.groupIndex !== null ? lane.groupIndex : boardPosition;
}

/**
 * A lane's hue as the inline style that hands it to CSS — the only thing callers need, so the
 * lookup above stays private. Custom properties are not part of React's `CSSProperties`, so the
 * one cast this needs lives here rather than at five call sites.
 */
export function laneHueStyle(index: number): CSSProperties {
  return { '--lane': laneHue(index) } as CSSProperties;
}
