package com.itime.app.data

import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow

enum class SyncPhase { Idle, Connecting, Syncing, Synced, Pending, Conflict, Offline, Error }

data class SyncState(
    val phase: SyncPhase = SyncPhase.Idle,
    val pendingCount: Int = 0,
    val conflictCount: Int = 0,
    val lastSuccessAt: Long? = null,
    val lastError: String? = null,
    val resetNotice: String? = null,
)

class SyncStatusHolder {
    private val _state = MutableStateFlow(SyncState())
    val state: StateFlow<SyncState> = _state.asStateFlow()

    fun update(transform: (SyncState) -> SyncState) {
        _state.value = transform(_state.value)
    }
}
