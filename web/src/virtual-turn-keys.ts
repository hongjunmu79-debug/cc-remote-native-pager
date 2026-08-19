export interface TurnKeySource {
  id: string;
}

export interface TurnKeySnapshot {
  readonly namespace: string;
  readonly keys: readonly string[];
  readonly getItemKey: (index: number) => string;
}

function hasSameKeys(
  snapshot: TurnKeySnapshot,
  turns: readonly TurnKeySource[],
  namespace: string,
): boolean {
  if (snapshot.namespace !== namespace) return false;
  if (snapshot.keys.length !== turns.length) return false;
  for (let index = 0; index < turns.length; index += 1) {
    if (snapshot.keys[index] !== `${namespace}\u0000${turns[index].id}`) {
      return false;
    }
  }
  return true;
}

/** Keep the virtualizer's key extractor stable while only turn contents stream,
 * but freeze a new immutable key list whenever prepend/rekey/reorder changes
 * identity. Old virtualizer options must never observe the next render's keys. */
export function updateTurnKeySnapshot(
  previous: TurnKeySnapshot | null,
  turns: readonly TurnKeySource[],
  namespace = "",
): TurnKeySnapshot {
  if (previous && hasSameKeys(previous, turns, namespace)) return previous;
  const keys = turns.map((turn) => `${namespace}\u0000${turn.id}`);
  return {
    namespace,
    keys,
    getItemKey: (index) => keys[index] ?? `missing-turn-${index}`,
  };
}
