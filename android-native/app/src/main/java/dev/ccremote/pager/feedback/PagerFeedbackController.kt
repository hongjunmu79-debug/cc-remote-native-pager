package dev.ccremote.pager.feedback

import android.Manifest
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.media.AudioManager
import android.media.ToneGenerator
import android.os.Build
import android.os.VibrationEffect
import android.os.Vibrator
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import androidx.core.content.ContextCompat
import dev.ccremote.pager.MainActivity
import dev.ccremote.pager.R
import dev.ccremote.pager.domain.PagerLifecycle
import dev.ccremote.pager.domain.PagerTask

class PagerFeedbackController(
    private val context: Context,
) : AutoCloseable {
    private val tone = ToneGenerator(AudioManager.STREAM_NOTIFICATION, 55)
    private val vibrator = context.getSystemService(Vibrator::class.java)

    init {
        val manager = context.getSystemService(NotificationManager::class.java)
        manager.createNotificationChannel(
            NotificationChannel(
                CHANNEL_TASKS,
                context.getString(R.string.notification_channel_tasks),
                NotificationManager.IMPORTANCE_DEFAULT,
            ).apply {
                description = context.getString(
                    R.string.notification_channel_tasks_description,
                )
                enableVibration(true)
            },
        )
    }

    fun notify(task: PagerTask) {
        val toneType = when (task.lifecycle) {
            PagerLifecycle.WAITING_ANSWER -> ToneGenerator.TONE_PROP_ACK
            PagerLifecycle.SUCCEEDED -> ToneGenerator.TONE_PROP_BEEP2
            PagerLifecycle.INTERRUPTED -> ToneGenerator.TONE_PROP_NACK
            else -> return
        }
        tone.startTone(toneType, 180)
        if (vibrator?.hasVibrator() == true) {
            vibrator.vibrate(VibrationEffect.createOneShot(120, 110))
        }
        postNotification(task)
    }

    private fun postNotification(task: PagerTask) {
        if (Build.VERSION.SDK_INT >= 33
            && ContextCompat.checkSelfPermission(
                context,
                Manifest.permission.POST_NOTIFICATIONS,
            ) != PackageManager.PERMISSION_GRANTED
        ) return
        val intent = Intent(context, MainActivity::class.java).apply {
            flags = Intent.FLAG_ACTIVITY_CLEAR_TOP or Intent.FLAG_ACTIVITY_SINGLE_TOP
            putExtra(MainActivity.EXTRA_OPEN_TASK_ID, task.id)
        }
        val pendingIntent = PendingIntent.getActivity(
            context,
            task.id.hashCode(),
            intent,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )
        val title = when (task.lifecycle) {
            PagerLifecycle.WAITING_ANSWER -> "等待你的回答"
            PagerLifecycle.SUCCEEDED -> "任务已完成"
            PagerLifecycle.INTERRUPTED -> "任务已中断"
            else -> return
        }
        val notification = NotificationCompat.Builder(context, CHANNEL_TASKS)
            .setSmallIcon(R.mipmap.ic_launcher)
            .setContentTitle(title)
            .setContentText(task.title)
            .setStyle(NotificationCompat.BigTextStyle().bigText(task.latestStep ?: task.title))
            .setContentIntent(pendingIntent)
            .setAutoCancel(true)
            .setOnlyAlertOnce(true)
            .setCategory(NotificationCompat.CATEGORY_STATUS)
            .build()
        NotificationManagerCompat.from(context).notify(task.id.hashCode(), notification)
    }

    override fun close() {
        tone.release()
    }

    private companion object {
        const val CHANNEL_TASKS = "agent_task_updates"
    }
}
