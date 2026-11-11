package com.itime.app.ui.screens.auth

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.CalendarMonth
import androidx.compose.material.icons.outlined.Visibility
import androidx.compose.material.icons.outlined.VisibilityOff
import androidx.compose.material3.Button
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.input.VisualTransformation
import androidx.compose.ui.unit.dp
import com.itime.app.data.remote.ApiException
import com.itime.app.data.repository.AuthRepository
import com.itime.app.data.session.SessionStore
import com.itime.app.ui.components.ErrorText
import kotlinx.coroutines.launch

@Composable
fun LoginScreen(
    auth: AuthRepository,
    sessionStore: SessionStore,
) {
    var registering by remember { mutableStateOf(false) }
    var email by remember { mutableStateOf("") }
    var password by remember { mutableStateOf("") }
    var showPassword by remember { mutableStateOf(false) }
    var showServer by remember { mutableStateOf(false) }
    var serverUrl by remember { mutableStateOf(sessionStore.baseUrl()) }
    var busy by remember { mutableStateOf(false) }
    var error by remember { mutableStateOf<String?>(null) }
    val scope = rememberCoroutineScope()

    fun submit() {
        if (busy) return
        if (email.isBlank() || password.isBlank()) {
            error = "请填写邮箱和密码。"
            return
        }
        scope.launch {
            busy = true
            error = null
            if (serverUrl.trim() != sessionStore.baseUrl()) {
                auth.updateBaseUrl(serverUrl.trim())
            }
            val result = if (registering) {
                auth.register(email, password, "iTime Android")
            } else {
                auth.login(email, password, "iTime Android")
            }
            busy = false
            result.onFailure { throwable ->
                error = when (throwable) {
                    is ApiException -> throwable.message
                    else -> throwable.message ?: "操作失败，请稍后重试。"
                }
            }
        }
    }

    Scaffold { padding ->
        Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(padding)
                .verticalScroll(rememberScrollState())
                .imePadding()
                .padding(horizontal = 24.dp),
            verticalArrangement = Arrangement.Center,
        ) {
            Spacer(Modifier.height(48.dp))
            Surface(
                color = MaterialTheme.colorScheme.primaryContainer,
                shape = MaterialTheme.shapes.large,
                modifier = Modifier.size(64.dp),
            ) {
                Icon(
                    Icons.Outlined.CalendarMonth,
                    contentDescription = null,
                    tint = MaterialTheme.colorScheme.onPrimaryContainer,
                    modifier = Modifier.padding(16.dp),
                )
            }
            Spacer(Modifier.height(24.dp))
            Text(
                if (registering) "创建 iTime 账号" else "登录 iTime",
                style = MaterialTheme.typography.headlineMedium,
            )
            Spacer(Modifier.height(8.dp))
            Text(
                "课程、任务与日程在手机和网页之间保持同步。",
                style = MaterialTheme.typography.bodyMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            Spacer(Modifier.height(32.dp))
            OutlinedTextField(
                value = email,
                onValueChange = { email = it },
                label = { Text("邮箱") },
                singleLine = true,
                keyboardOptions = KeyboardOptions(
                    keyboardType = KeyboardType.Email,
                    imeAction = ImeAction.Next,
                ),
                modifier = Modifier.fillMaxWidth(),
            )
            Spacer(Modifier.height(12.dp))
            OutlinedTextField(
                value = password,
                onValueChange = { password = it },
                label = { Text("密码") },
                singleLine = true,
                visualTransformation = if (showPassword) {
                    VisualTransformation.None
                } else {
                    PasswordVisualTransformation()
                },
                trailingIcon = {
                    IconButton(onClick = { showPassword = !showPassword }) {
                        Icon(
                            if (showPassword) Icons.Outlined.VisibilityOff else Icons.Outlined.Visibility,
                            contentDescription = "切换密码可见性",
                        )
                    }
                },
                keyboardOptions = KeyboardOptions(
                    keyboardType = KeyboardType.Password,
                    imeAction = ImeAction.Done,
                ),
                modifier = Modifier.fillMaxWidth(),
            )
            Spacer(Modifier.height(8.dp))
            ErrorText(error)
            Spacer(Modifier.height(8.dp))
            Button(
                onClick = ::submit,
                enabled = !busy,
                modifier = Modifier
                    .fillMaxWidth()
                    .height(52.dp),
            ) {
                if (busy) {
                    CircularProgressIndicator(
                        modifier = Modifier.size(20.dp),
                        strokeWidth = 2.dp,
                        color = MaterialTheme.colorScheme.onPrimary,
                    )
                } else {
                    Text(if (registering) "注册并登录" else "登录")
                }
            }
            Spacer(Modifier.height(4.dp))
            TextButton(
                onClick = {
                    registering = !registering
                    error = null
                },
                modifier = Modifier.align(Alignment.CenterHorizontally),
            ) {
                Text(if (registering) "已有账号，去登录" else "没有账号？创建一个")
            }
            TextButton(
                onClick = { showServer = !showServer },
                modifier = Modifier.align(Alignment.CenterHorizontally),
            ) {
                Text(if (showServer) "收起服务器设置" else "服务器设置")
            }
            if (showServer) {
                OutlinedTextField(
                    value = serverUrl,
                    onValueChange = { serverUrl = it },
                    label = { Text("服务器地址") },
                    supportingText = { Text("模拟器默认 10.0.2.2:8000，真机填电脑局域网地址。") },
                    singleLine = true,
                    modifier = Modifier.fillMaxWidth(),
                )
                Spacer(Modifier.height(8.dp))
            }
            Spacer(Modifier.height(48.dp))
        }
    }
}
