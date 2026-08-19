package dev.ccremote.pager.domain

import org.junit.Assert.assertEquals
import org.junit.Assert.assertSame
import org.junit.Test

class PagerEngineFilterTest {
    private fun task(id: String, engine: PagerEngine) = PagerTask(
        id = id,
        engine = engine,
        projectName = "project",
        title = id,
        lifecycle = PagerLifecycle.IDLE,
        startedAt = 1L,
        updatedAt = 1L,
        pinned = false,
        focused = false,
        capabilities = emptySet(),
        subagents = emptyList(),
    )

    @Test
    fun allKeepsOriginalProjectionAndOrder() {
        val tasks = listOf(
            task("claude-1", PagerEngine.CLAUDE),
            task("codex-1", PagerEngine.CODEX),
        )

        assertSame(tasks, tasks.forEngine(PagerEngineFilter.ALL))
    }

    @Test
    fun engineFiltersAreExactAndStable() {
        val tasks = listOf(
            task("claude-1", PagerEngine.CLAUDE),
            task("codex-1", PagerEngine.CODEX),
            task("claude-2", PagerEngine.CLAUDE),
        )

        assertEquals(
            listOf("claude-1", "claude-2"),
            tasks.forEngine(PagerEngineFilter.CLAUDE).map(PagerTask::id),
        )
        assertEquals(
            listOf("codex-1"),
            tasks.forEngine(PagerEngineFilter.CODEX).map(PagerTask::id),
        )
    }
}
