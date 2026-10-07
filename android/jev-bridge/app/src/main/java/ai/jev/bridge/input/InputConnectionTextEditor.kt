// Derived from droidrun/mobilerun-portal; AGPL-3.0-or-later. See NOTICE.md.
package ai.jev.bridge.input

import android.view.inputmethod.ExtractedTextRequest
import android.view.inputmethod.InputConnection

internal sealed class TextInputResult {
    data object Verified : TextInputResult()
    data object Rejected : TextInputResult()
    data object AcceptedUnverified : TextInputResult()
    data object CommitOutcomeUnknown : TextInputResult()
    data object InputSessionChanged : TextInputResult()
}

internal class InputConnectionTextEditor(
    private val connectionProvider: () -> InputConnection?,
    private val generationProvider: () -> Long,
    private val sleep: (Long) -> Unit,
    private val retryDelayMs: Long = DEFAULT_RETRY_DELAY_MS,
) {
    companion object {
        private const val DEFAULT_RETRY_DELAY_MS = 100L
        private const val MAX_ATTEMPTS = 2
        private const val ZERO_WIDTH_SPACE = '\u200B'
        private const val ZERO_WIDTH_NO_BREAK_SPACE = '\uFEFF'
    }

    private data class Snapshot(
        val text: String,
        val startOffset: Int,
        val partialStartOffset: Int,
        val selectionStart: Int,
        val selectionEnd: Int,
        val reachesFieldEnd: Boolean,
    ) {
        val isCompleteReport: Boolean
            get() = partialStartOffset == -1

        val coversEntireField: Boolean
            get() = startOffset == 0 && isCompleteReport && reachesFieldEnd
    }

    fun inputText(text: String, clear: Boolean): TextInputResult {
        val inputSessionGeneration = generationProvider()

        repeat(MAX_ATTEMPTS) { attempt ->
            if (!isSameInputSession(inputSessionGeneration)) {
                return TextInputResult.InputSessionChanged
            }

            val connection = connectionOrNull()
            if (connection == null) {
                if (attempt < MAX_ATTEMPTS - 1) {
                    sleep(retryDelayMs)
                    return@repeat
                }
                return TextInputResult.Rejected
            }

            if (!isSameInputSession(inputSessionGeneration)) {
                return TextInputResult.InputSessionChanged
            }
            val compositionFinished = try {
                connection.finishComposingText()
            } catch (_: Exception) {
                false
            }
            if (!compositionFinished) {
                if (!clear) {
                    when (val result = commitWithoutVerification(connection, text, inputSessionGeneration)) {
                        TextInputResult.Rejected -> {
                            if (attempt < MAX_ATTEMPTS - 1) {
                                sleep(retryDelayMs)
                                return@repeat
                            }
                            return TextInputResult.Rejected
                        }
                        else -> return result
                    }
                }
                if (!isSameInputSession(inputSessionGeneration)) {
                    return TextInputResult.InputSessionChanged
                }
                if (attempt < MAX_ATTEMPTS - 1) {
                    sleep(retryDelayMs)
                    return@repeat
                }
                return TextInputResult.Rejected
            }
            if (!isSameInputSession(inputSessionGeneration)) {
                return TextInputResult.InputSessionChanged
            }

            val before = readSnapshot(connection, requireFieldEnd = clear)
            if (before == null && !clear) {
                when (val result = commitWithoutVerification(connection, text, inputSessionGeneration)) {
                    TextInputResult.Rejected -> {
                        if (attempt < MAX_ATTEMPTS - 1) {
                            sleep(retryDelayMs)
                            return@repeat
                        }
                        return TextInputResult.Rejected
                    }
                    else -> return result
                }
            }

            if (before == null || (clear && !before.coversEntireField)) {
                if (attempt < MAX_ATTEMPTS - 1) {
                    sleep(retryDelayMs)
                    return@repeat
                }
                return TextInputResult.Rejected
            }

            val selection = selectionFor(before, clear)
            val expected = expectedText(before, text, clear)
            val selectionAccepted = try {
                connection.setSelection(selection.first, selection.second)
            } catch (_: Exception) {
                false
            }
            if (!selectionAccepted) {
                if (!isSameInputSession(inputSessionGeneration)) {
                    return TextInputResult.InputSessionChanged
                }
                if (attempt < MAX_ATTEMPTS - 1) {
                    sleep(retryDelayMs)
                    return@repeat
                }
                return TextInputResult.Rejected
            }

            if (!isSameInputSession(inputSessionGeneration)) {
                return TextInputResult.InputSessionChanged
            }
            val commitAccepted = try {
                connection.commitText(text, 1)
            } catch (_: Exception) {
                return if (isSameInputSession(inputSessionGeneration)) {
                    TextInputResult.CommitOutcomeUnknown
                } else {
                    TextInputResult.InputSessionChanged
                }
            }
            if (!commitAccepted) {
                if (!isSameInputSession(inputSessionGeneration)) {
                    return TextInputResult.InputSessionChanged
                }
                if (attempt < MAX_ATTEMPTS - 1) {
                    sleep(retryDelayMs)
                    return@repeat
                }
                return TextInputResult.Rejected
            }

            sleep(retryDelayMs)
            if (!isSameInputSession(inputSessionGeneration)) {
                return TextInputResult.InputSessionChanged
            }
            val afterConnection = connectionOrNull()
            val after = afterConnection?.let {
                readSnapshot(it, requireFieldEnd = clear)
            }
            if (!isSameInputSession(inputSessionGeneration)) {
                return TextInputResult.InputSessionChanged
            }
            if (after != null &&
                canVerify(before, after, clear) &&
                matches(after.text, expected, text)
            ) {
                return TextInputResult.Verified
            }

            return TextInputResult.AcceptedUnverified
        }

        return TextInputResult.Rejected
    }

    private fun isSameInputSession(generation: Long): Boolean {
        return generationProvider() == generation
    }

    private fun commitWithoutVerification(
        connection: InputConnection,
        text: String,
        inputSessionGeneration: Long,
    ): TextInputResult {
        if (!isSameInputSession(inputSessionGeneration)) {
            return TextInputResult.InputSessionChanged
        }
        val accepted = try {
            connection.commitText(text, 1)
        } catch (_: Exception) {
            return if (isSameInputSession(inputSessionGeneration)) {
                TextInputResult.CommitOutcomeUnknown
            } else {
                TextInputResult.InputSessionChanged
            }
        }
        if (!isSameInputSession(inputSessionGeneration)) {
            return TextInputResult.InputSessionChanged
        }
        return if (accepted) TextInputResult.AcceptedUnverified else TextInputResult.Rejected
    }

    private fun connectionOrNull(): InputConnection? {
        return try {
            connectionProvider()
        } catch (_: Exception) {
            null
        }
    }

    private fun readSnapshot(
        connection: InputConnection,
        requireFieldEnd: Boolean = false,
    ): Snapshot? {
        return try {
            val extracted = connection.getExtractedText(ExtractedTextRequest(), 0) ?: return null
            val value = extracted.text?.toString() ?: ""
            val relativeStart = extracted.selectionStart
            val relativeEnd = extracted.selectionEnd
            if (relativeStart < 0 || relativeEnd < 0 || relativeStart > value.length || relativeEnd > value.length) {
                return null
            }
            val structurallyComplete = extracted.startOffset == 0 && extracted.partialStartOffset == -1
            val fieldEndConfirmed = !requireFieldEnd ||
                (structurallyComplete && reachesFieldEnd(connection, value, relativeStart, relativeEnd))
            Snapshot(
                text = value,
                startOffset = extracted.startOffset,
                partialStartOffset = extracted.partialStartOffset,
                selectionStart = relativeStart,
                selectionEnd = relativeEnd,
                reachesFieldEnd = fieldEndConfirmed,
            )
        } catch (_: Exception) {
            return null
        }
    }

    private fun reachesFieldEnd(
        connection: InputConnection,
        text: String,
        selectionStart: Int,
        selectionEnd: Int,
    ): Boolean {
        val cursor = maxOf(selectionStart, selectionEnd)
        val representedSuffix = text.substring(cursor)
        val actualSuffix = connection.getTextAfterCursor(representedSuffix.length + 1, 0)
            ?.toString()
            ?: return false
        return actualSuffix == representedSuffix
    }

    private fun selectionFor(snapshot: Snapshot, clear: Boolean): Pair<Int, Int> {
        val relativeStart = if (clear) 0 else minOf(snapshot.selectionStart, snapshot.selectionEnd)
        val relativeEnd = if (clear) snapshot.text.length else maxOf(snapshot.selectionStart, snapshot.selectionEnd)
        return Pair(snapshot.startOffset + relativeStart, snapshot.startOffset + relativeEnd)
    }

    private fun expectedText(snapshot: Snapshot, text: String, clear: Boolean): String {
        if (clear) return text

        val selectionStart = minOf(snapshot.selectionStart, snapshot.selectionEnd)
        val selectionEnd = maxOf(snapshot.selectionStart, snapshot.selectionEnd)
        return snapshot.text.replaceRange(selectionStart, selectionEnd, text)
    }

    private fun canVerify(
        before: Snapshot,
        after: Snapshot,
        clear: Boolean,
    ): Boolean {
        if (clear) return after.coversEntireField
        return before.isCompleteReport &&
            after.isCompleteReport &&
            before.startOffset == after.startOffset
    }

    private fun matches(actual: String, expected: String, requested: String): Boolean {
        if (requested.any(::isIgnoredSentinel)) {
            return actual == expected
        }
        return normalize(actual) == normalize(expected)
    }

    private fun normalize(value: String): String {
        return value.filterNot(::isIgnoredSentinel)
    }

    private fun isIgnoredSentinel(value: Char): Boolean {
        return value == ZERO_WIDTH_SPACE || value == ZERO_WIDTH_NO_BREAK_SPACE
    }
}
