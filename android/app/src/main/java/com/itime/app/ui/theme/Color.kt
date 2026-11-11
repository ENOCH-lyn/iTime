package com.itime.app.ui.theme

import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.ui.graphics.Color

/**
 * Fallback Material 3 palette derived from the iTime brand seed.
 *
 * The app prefers the platform dynamic palette (Material You) on Android 12+;
 * these roles are only used when dynamic colour is unavailable.
 */
private val SeedPrimary = Color(0xFF4F5B92)
private val SeedPrimaryContainer = Color(0xFFDBE1FF)
private val SeedOnPrimaryContainer = Color(0xFF07194B)
private val SeedSecondary = Color(0xFF5B5D72)
private val SeedSecondaryContainer = Color(0xFFE0E1F9)
private val SeedTertiary = Color(0xFF77536D)
private val SeedTertiaryContainer = Color(0xFFFFD7F0)
private val SeedError = Color(0xFFBA1A1A)
private val SeedErrorContainer = Color(0xFFFFDAD6)

val ItimeLightColors = lightColorScheme(
    primary = SeedPrimary,
    onPrimary = Color.White,
    primaryContainer = SeedPrimaryContainer,
    onPrimaryContainer = SeedOnPrimaryContainer,
    secondary = SeedSecondary,
    onSecondary = Color.White,
    secondaryContainer = SeedSecondaryContainer,
    onSecondaryContainer = Color(0xFF181A2C),
    tertiary = SeedTertiary,
    onTertiary = Color.White,
    tertiaryContainer = SeedTertiaryContainer,
    onTertiaryContainer = Color(0xFF2C1229),
    error = SeedError,
    onError = Color.White,
    errorContainer = SeedErrorContainer,
    onErrorContainer = Color(0xFF410002),
    background = Color(0xFFFEFBFF),
    onBackground = Color(0xFF1B1B1F),
    surface = Color(0xFFFEFBFF),
    onSurface = Color(0xFF1B1B1F),
    surfaceVariant = Color(0xFFE3E1EC),
    onSurfaceVariant = Color(0xFF46464F),
    outline = Color(0xFF777680),
    outlineVariant = Color(0xFFC7C5D0),
    inverseSurface = Color(0xFF303034),
    inverseOnSurface = Color(0xFFF3F0F4),
    inversePrimary = Color(0xFFB9C3FF),
    surfaceTint = SeedPrimary,
    scrim = Color.Black,
)

val ItimeDarkColors = darkColorScheme(
    primary = Color(0xFFB9C3FF),
    onPrimary = Color(0xFF1E2B5F),
    primaryContainer = Color(0xFF364179),
    onPrimaryContainer = SeedPrimaryContainer,
    secondary = Color(0xFFC4C5DD),
    onSecondary = Color(0xFF2D2F42),
    secondaryContainer = Color(0xFF434559),
    onSecondaryContainer = SeedSecondaryContainer,
    tertiary = Color(0xFFE6BADF),
    onTertiary = Color(0xFF44263F),
    tertiaryContainer = Color(0xFF5D3C56),
    onTertiaryContainer = SeedTertiaryContainer,
    error = Color(0xFFFFB4AB),
    onError = Color(0xFF690005),
    errorContainer = Color(0xFF93000A),
    onErrorContainer = SeedErrorContainer,
    background = Color(0xFF1B1B1F),
    onBackground = Color(0xFFE4E1E6),
    surface = Color(0xFF1B1B1F),
    onSurface = Color(0xFFE4E1E6),
    surfaceVariant = Color(0xFF46464F),
    onSurfaceVariant = Color(0xFFC7C5D0),
    outline = Color(0xFF918F9A),
    outlineVariant = Color(0xFF46464F),
    inverseSurface = Color(0xFFE4E1E6),
    inverseOnSurface = Color(0xFF303034),
    inversePrimary = SeedPrimary,
    surfaceTint = Color(0xFFB9C3FF),
    scrim = Color.Black,
)
