package dev.ccremote.pager.domain

/** Stable dashboard filter state; kept outside Compose so it stays testable. */
enum class PagerEngineFilter(val engine: PagerEngine?) {
    ALL(null),
    CLAUDE(PagerEngine.CLAUDE),
    CODEX(PagerEngine.CODEX),
}

fun List<PagerTask>.forEngine(filter: PagerEngineFilter): List<PagerTask> {
    val engine = filter.engine ?: return this
    return filter { task -> task.engine == engine }
}
