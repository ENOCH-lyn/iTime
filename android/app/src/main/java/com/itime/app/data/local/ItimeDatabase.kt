package com.itime.app.data.local

import android.content.Context
import androidx.room.Database
import androidx.room.Room
import androidx.room.RoomDatabase

@Database(
    entities = [
        TaskEntity::class,
        StepEntity::class,
        ProjectEntity::class,
        TermEntity::class,
        TermPeriodEntity::class,
        SeriesEntity::class,
        ExceptionEntity::class,
        AdditionEntity::class,
        OccurrenceEntity::class,
        TimeSettingsEntity::class,
        SyncMetaEntity::class,
        OutboxEntity::class,
    ],
    version = 1,
    exportSchema = true,
)
abstract class ItimeDatabase : RoomDatabase() {
    abstract fun taskDao(): TaskDao
    abstract fun stepDao(): StepDao
    abstract fun projectDao(): ProjectDao
    abstract fun termDao(): TermDao
    abstract fun seriesDao(): SeriesDao
    abstract fun exceptionDao(): ExceptionDao
    abstract fun additionDao(): AdditionDao
    abstract fun occurrenceDao(): OccurrenceDao
    abstract fun settingsDao(): SettingsDao
    abstract fun outboxDao(): OutboxDao

    companion object {
        fun build(context: Context, name: String = "itime.db"): ItimeDatabase =
            Room.databaseBuilder(context, ItimeDatabase::class.java, name)
                .fallbackToDestructiveMigration()
                .build()
    }
}
