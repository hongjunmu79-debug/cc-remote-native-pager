package dev.ccremote.pager.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.selection.selectable
import androidx.compose.foundation.selection.selectableGroup
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.BorderStroke
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Button
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Surface
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.semantics.stateDescription
import androidx.compose.ui.unit.dp
import dev.ccremote.pager.PagerUiState
import dev.ccremote.pager.domain.PagerEngine
import dev.ccremote.pager.domain.PagerEngineFilter
import dev.ccremote.pager.domain.PagerTask
import dev.ccremote.pager.domain.forEngine

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun PagerDashboard(
    state: PagerUiState,
    onOpenChat: () -> Unit,
    onRefresh: () -> Unit,
    onOpenTask: (PagerTask) -> Unit,
    onInterrupt: (PagerTask) -> Unit,
    onAnswer: (PagerTask, String) -> Unit,
    onPin: (PagerTask, Boolean) -> Unit,
    onMarkRead: (PagerTask) -> Unit,
    onUpdateEndpoint: (String) -> Unit,
    onFeedbackEnabled: (Boolean) -> Unit,
) {
    var expandedTaskId by rememberSaveable { mutableStateOf<String?>(null) }
    var settingsOpen by rememberSaveable { mutableStateOf(false) }
    var engineFilter by rememberSaveable { mutableStateOf(PagerEngineFilter.ALL) }
    val visibleTasks = remember(state.tasks, engineFilter) {
        state.tasks.forEngine(engineFilter)
    }
    val listState = rememberLazyListState()

    LaunchedEffect(engineFilter) {
        expandedTaskId = null
        if (visibleTasks.isNotEmpty()) listState.scrollToItem(0)
    }

    Scaffold(
        containerColor = MaterialTheme.colorScheme.background,
        topBar = {
            TopAppBar(
                title = {
                    Column {
                        Text(
                            "AGENT DECK",
                            fontFamily = FontFamily.Monospace,
                            fontWeight = FontWeight.Bold,
                        )
                        Text(
                            state.machineId.ifBlank { "等待网页端登录" },
                            style = MaterialTheme.typography.labelSmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                            maxLines = 1,
                            overflow = TextOverflow.Ellipsis,
                        )
                    }
                },
                actions = {
                    TextButton(onClick = onRefresh) { Text("刷新") }
                    TextButton(onClick = { settingsOpen = true }) { Text("设置") }
                    TextButton(onClick = onOpenChat) { Text("聊天") }
                },
            )
        },
    ) { padding ->
        Column(
            Modifier.fillMaxSize().padding(padding),
        ) {
            ConnectionBanner(state)
            if (state.tasks.isEmpty()) {
                EmptyDashboard(
                    bridgeConnected = state.bridgeConnected,
                    onOpenChat = onOpenChat,
                    onRefresh = onRefresh,
                )
            } else {
                EngineSummary(
                    tasks = state.tasks,
                    selected = engineFilter,
                    onSelected = { engineFilter = it },
                )
                if (visibleTasks.isEmpty()) {
                    FilteredDashboardEmpty(engineFilter)
                } else {
                    LazyColumn(
                        state = listState,
                        modifier = Modifier.fillMaxSize(),
                        contentPadding = androidx.compose.foundation.layout.PaddingValues(
                            start = 14.dp,
                            end = 14.dp,
                            top = 10.dp,
                            bottom = 28.dp,
                        ),
                        verticalArrangement = Arrangement.spacedBy(10.dp),
                    ) {
                        items(visibleTasks, key = PagerTask::id) { task ->
                            PagerTaskCard(
                                task = task,
                                expanded = expandedTaskId == task.id,
                                onToggleExpanded = {
                                    expandedTaskId = if (expandedTaskId == task.id) null else task.id
                                },
                                onOpenChat = { onOpenTask(task) },
                                onInterrupt = { onInterrupt(task) },
                                onAnswer = { onAnswer(task, it) },
                                onPin = { onPin(task, it) },
                                onMarkRead = { onMarkRead(task) },
                            )
                        }
                    }
                }
            }
        }
    }

    if (settingsOpen) {
        PagerSettingsDialog(
            initialUrl = state.endpoint.url,
            feedbackEnabled = state.feedbackEnabled,
            onDismiss = { settingsOpen = false },
            onSave = { url ->
                onUpdateEndpoint(url)
                settingsOpen = false
            },
            onFeedbackEnabled = onFeedbackEnabled,
        )
    }
}

@Composable
private fun EngineSummary(
    tasks: List<PagerTask>,
    selected: PagerEngineFilter,
    onSelected: (PagerEngineFilter) -> Unit,
) {
    val claudeCount = tasks.count { it.engine == PagerEngine.CLAUDE }
    val codexCount = tasks.count { it.engine == PagerEngine.CODEX }
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .padding(horizontal = 16.dp, vertical = 4.dp)
            .selectableGroup(),
        horizontalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        EngineFilterCount(
            label = "全部",
            count = tasks.size,
            color = MaterialTheme.colorScheme.primary,
            selected = selected == PagerEngineFilter.ALL,
            onClick = { onSelected(PagerEngineFilter.ALL) },
            modifier = Modifier.weight(1f),
        )
        EngineFilterCount(
            label = "CLAUDE",
            count = claudeCount,
            color = PagerColors.Green,
            selected = selected == PagerEngineFilter.CLAUDE,
            onClick = { onSelected(PagerEngineFilter.CLAUDE) },
            modifier = Modifier.weight(1f),
        )
        EngineFilterCount(
            label = "CODEX",
            count = codexCount,
            color = PagerColors.Cyan,
            selected = selected == PagerEngineFilter.CODEX,
            onClick = { onSelected(PagerEngineFilter.CODEX) },
            modifier = Modifier.weight(1f),
        )
    }
}

@Composable
private fun EngineFilterCount(
    label: String,
    count: Int,
    color: androidx.compose.ui.graphics.Color,
    selected: Boolean,
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
) {
    Surface(
        modifier = modifier
            .selectable(
                selected = selected,
                role = Role.Tab,
                onClick = onClick,
            )
            .semantics {
                stateDescription = if (selected) "已选择" else "未选择"
            },
        color = if (selected) color.copy(alpha = 0.16f)
        else MaterialTheme.colorScheme.surfaceVariant,
        border = if (selected) BorderStroke(1.dp, color) else null,
        shape = MaterialTheme.shapes.medium,
    ) {
        Column(
            modifier = Modifier.padding(horizontal = 10.dp, vertical = 9.dp),
            horizontalAlignment = Alignment.CenterHorizontally,
        ) {
            Text(
                label,
                color = color,
                fontFamily = FontFamily.Monospace,
                fontWeight = FontWeight.Bold,
                style = MaterialTheme.typography.labelMedium,
                maxLines = 1,
            )
            Text(
                count.toString(),
                style = MaterialTheme.typography.titleMedium,
                fontWeight = FontWeight.Bold,
            )
        }
    }
}

@Composable
private fun FilteredDashboardEmpty(filter: PagerEngineFilter) {
    val engine = when (filter) {
        PagerEngineFilter.ALL -> "任务"
        PagerEngineFilter.CLAUDE -> "Claude 会话"
        PagerEngineFilter.CODEX -> "Codex 会话"
    }
    Box(
        modifier = Modifier.fillMaxSize().padding(28.dp),
        contentAlignment = Alignment.Center,
    ) {
        Text(
            "当前没有$engine",
            style = MaterialTheme.typography.bodyLarge,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
    }
}

@Composable
private fun ConnectionBanner(state: PagerUiState) {
    val label = when {
        !state.bridgeConnected -> "原生桥已失联 · 网页可能正在登录或重载"
        !state.wrapperOnline -> "电脑端 Wrapper 离线"
        else -> "连接正常 · ${state.tasks.size} 个任务"
    }
    val color = when {
        !state.bridgeConnected -> PagerColors.Red
        !state.wrapperOnline -> PagerColors.Yellow
        else -> PagerColors.Green
    }
    Row(
        Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 8.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        androidx.compose.foundation.Canvas(Modifier.size(18.dp).padding(end = 8.dp)) {
            drawCircle(color, radius = 5.dp.toPx())
        }
        Text(
            label,
            style = MaterialTheme.typography.labelMedium,
            color = color,
            fontFamily = FontFamily.Monospace,
        )
    }
    HorizontalDivider(color = MaterialTheme.colorScheme.outlineVariant)
}

@Composable
private fun EmptyDashboard(
    bridgeConnected: Boolean,
    onOpenChat: () -> Unit,
    onRefresh: () -> Unit,
) {
    Box(Modifier.fillMaxSize().padding(28.dp), contentAlignment = Alignment.Center) {
        Column(horizontalAlignment = Alignment.CenterHorizontally) {
            PixelCore(
                lifecycle = if (bridgeConnected)
                    dev.ccremote.pager.domain.PagerLifecycle.IDLE
                else dev.ccremote.pager.domain.PagerLifecycle.OFFLINE,
                activity = null,
                modifier = Modifier.size(120.dp),
            )
            Spacer(Modifier.height(18.dp))
            Text(
                if (bridgeConnected) "当前没有可显示的任务" else "请先在聊天页完成登录",
                style = MaterialTheme.typography.titleMedium,
            )
            Spacer(Modifier.height(8.dp))
            Text(
                if (bridgeConnected) "新建或恢复会话后，看板会自动更新。"
                else "WebView 保持 cc-remote 的安全登录和连接状态。",
                style = MaterialTheme.typography.bodyMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            Spacer(Modifier.height(20.dp))
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                Button(onClick = onOpenChat) { Text("打开聊天") }
                OutlinedButton(onClick = onRefresh) { Text("重新同步") }
            }
        }
    }
}

@Composable
private fun PagerSettingsDialog(
    initialUrl: String,
    feedbackEnabled: Boolean,
    onDismiss: () -> Unit,
    onSave: (String) -> Unit,
    onFeedbackEnabled: (Boolean) -> Unit,
) {
    var url by rememberSaveable(initialUrl) { mutableStateOf(initialUrl) }
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("原生看板设置") },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(14.dp)) {
                OutlinedTextField(
                    value = url,
                    onValueChange = { url = it.take(2_048) },
                    label = { Text("cc-remote 地址") },
                    supportingText = { Text("局域网 HTTP 仅允许当前 192.168.3.4 主机") },
                    singleLine = true,
                    modifier = Modifier.fillMaxWidth(),
                )
                Row(
                    Modifier.fillMaxWidth(),
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.SpaceBetween,
                ) {
                    Column(Modifier.weight(1f)) {
                        Text("声音、震动和系统提醒")
                        Text(
                            "任务完成、异常或等待回答时提醒",
                            style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                    }
                    Switch(
                        checked = feedbackEnabled,
                        onCheckedChange = onFeedbackEnabled,
                    )
                }
            }
        },
        confirmButton = {
            Button(onClick = { onSave(url.trim()) }, enabled = url.isNotBlank()) {
                Text("保存并重载")
            }
        },
        dismissButton = { TextButton(onClick = onDismiss) { Text("取消") } },
    )
}
