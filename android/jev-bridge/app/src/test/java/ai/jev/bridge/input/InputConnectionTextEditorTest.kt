// Derived from droidrun/mobilerun-portal; AGPL-3.0-or-later. See NOTICE.md.
package ai.jev.bridge.input

import android.view.inputmethod.ExtractedText
import android.view.inputmethod.InputConnection
import io.mockk.every
import io.mockk.mockk
import io.mockk.verify
import io.mockk.verifyOrder
import org.junit.Assert.assertEquals
import org.junit.Test

class InputConnectionTextEditorTest {
    @Test
    fun clear_replacesEntireFieldAtomically() {
        val connection = mockk<InputConnection>(relaxed = true)
        every { connection.getExtractedText(any(), 0) } returnsMany listOf(
            snapshot("old", selectionStart = 3),
            snapshot("new", selectionStart = 3),
        )
        every { connection.getTextAfterCursor(any(), 0) } returns ""
        every { connection.finishComposingText() } returns true
        every { connection.setSelection(0, 3) } returns true
        every { connection.commitText("new", 1) } returns true
        val editor = editor { connection }

        assertEquals(TextInputResult.Verified, editor.inputText("new", clear = true))

        verify(exactly = 1) { connection.setSelection(0, 3) }
        verify(exactly = 1) { connection.commitText("new", 1) }
        verify(exactly = 0) { connection.deleteSurroundingText(any(), any()) }
    }

    @Test
    fun clear_rejectsExtractedWindowThatDoesNotStartAtFieldBeginning() {
        val connection = mockk<InputConnection>(relaxed = true)
        every { connection.getExtractedText(any(), 0) } returns
            snapshot("old", startOffset = 7, selectionStart = 3)
        every { connection.finishComposingText() } returns true
        val editor = editor { connection }

        assertEquals(TextInputResult.Rejected, editor.inputText("new", clear = true))

        verify(exactly = 0) { connection.setSelection(any(), any()) }
        verify(exactly = 0) { connection.commitText(any(), any()) }
    }

    @Test
    fun clear_rejectsPartialChangeReport() {
        val connection = mockk<InputConnection>(relaxed = true)
        every { connection.getExtractedText(any(), 0) } returns
            snapshot("old", partialStartOffset = 0, selectionStart = 3)
        every { connection.finishComposingText() } returns true
        val editor = editor { connection }

        assertEquals(TextInputResult.Rejected, editor.inputText("new", clear = true))

        verify(exactly = 0) { connection.setSelection(any(), any()) }
        verify(exactly = 0) { connection.commitText(any(), any()) }
    }

    @Test
    fun clear_rejectsSnapshotWithUnreportedSuffix() {
        val connection = mockk<InputConnection>(relaxed = true)
        every { connection.getExtractedText(any(), 0) } returns
            snapshot("prefix", selectionStart = 6)
        every { connection.getTextAfterCursor(1, 0) } returns "x"
        every { connection.finishComposingText() } returns true
        val editor = editor { connection }

        assertEquals(TextInputResult.Rejected, editor.inputText("new", clear = true))

        verify(exactly = 0) { connection.setSelection(any(), any()) }
        verify(exactly = 0) { connection.commitText(any(), any()) }
    }

    @Test
    fun append_replacesSelectionAndIgnoresZeroWidthSentinels() {
        val connection = mockk<InputConnection>(relaxed = true)
        every { connection.getExtractedText(any(), 0) } returnsMany listOf(
            snapshot("\u200BabcXYZ", selectionStart = 4, selectionEnd = 7),
            snapshot("\uFEFFabc9911656022", selectionStart = 13),
        )
        every { connection.finishComposingText() } returns true
        every { connection.setSelection(4, 7) } returns true
        every { connection.commitText("9911656022", 1) } returns true
        val editor = editor { connection }

        assertEquals(
            TextInputResult.Verified,
            editor.inputText("9911656022", clear = false),
        )
        verify(exactly = 1) { connection.commitText("9911656022", 1) }
    }

    @Test
    fun clear_doesNotVerifyDroppedRequestedZeroWidthCharacters() {
        val connection = mockk<InputConnection>(relaxed = true)
        every { connection.getExtractedText(any(), 0) } returns snapshot("", selectionStart = 0)
        every { connection.getTextAfterCursor(any(), 0) } returns ""
        every { connection.finishComposingText() } returns true
        every { connection.setSelection(0, 0) } returns true
        every { connection.commitText("\u200B\uFEFF", 1) } returns true
        val editor = editor { connection }

        assertEquals(
            TextInputResult.AcceptedUnverified,
            editor.inputText("\u200B\uFEFF", clear = true),
        )
        verify(exactly = 1) { connection.commitText("\u200B\uFEFF", 1) }
    }

    @Test
    fun clear_verifiesRequestedZeroWidthCharactersWhenPresent() {
        val connection = mockk<InputConnection>(relaxed = true)
        every { connection.getExtractedText(any(), 0) } returnsMany listOf(
            snapshot("", selectionStart = 0),
            snapshot("\u200B\uFEFF", selectionStart = 2),
        )
        every { connection.getTextAfterCursor(any(), 0) } returns ""
        every { connection.finishComposingText() } returns true
        every { connection.setSelection(0, 0) } returns true
        every { connection.commitText("\u200B\uFEFF", 1) } returns true
        val editor = editor { connection }

        assertEquals(
            TextInputResult.Verified,
            editor.inputText("\u200B\uFEFF", clear = true),
        )
        verify(exactly = 1) { connection.commitText("\u200B\uFEFF", 1) }
    }

    @Test
    fun acceptedAppendWithStalePostState_isNotRetried() {
        val connection = mockk<InputConnection>(relaxed = true)
        every { connection.getExtractedText(any(), 0) } returnsMany listOf(
            snapshot("hello", selectionStart = 5),
            snapshot("hello", selectionStart = 5),
        )
        every { connection.finishComposingText() } returns true
        every { connection.setSelection(5, 5) } returns true
        every { connection.commitText(" world", 1) } returns true
        val editor = editor { connection }

        assertEquals(
            TextInputResult.AcceptedUnverified,
            editor.inputText(" world", clear = false),
        )
        verify(exactly = 1) { connection.commitText(" world", 1) }
    }

    @Test
    fun acceptedAppendIsNotVerifiedByPreexistingSubstring() {
        val connection = mockk<InputConnection>(relaxed = true)
        every { connection.getExtractedText(any(), 0) } returnsMany listOf(
            snapshot("1", selectionStart = 1),
            snapshot("1", selectionStart = 1),
        )
        every { connection.finishComposingText() } returns true
        every { connection.setSelection(1, 1) } returns true
        every { connection.commitText("1", 1) } returns true
        val editor = editor { connection }

        assertEquals(
            TextInputResult.AcceptedUnverified,
            editor.inputText("1", clear = false),
        )
        verify(exactly = 1) { connection.commitText("1", 1) }
    }

    @Test
    fun acceptedAppendWithoutReadablePostState_isNotRetried() {
        val connection = mockk<InputConnection>(relaxed = true)
        every { connection.getExtractedText(any(), 0) } returns snapshot("", selectionStart = 0)
        every { connection.finishComposingText() } returns true
        every { connection.setSelection(0, 0) } returns true
        every { connection.commitText("hello", 1) } returns true
        val connections = ArrayDeque<InputConnection?>(listOf(connection, null))
        val editor = editor { connections.removeFirstOrNull() }

        assertEquals(
            TextInputResult.AcceptedUnverified,
            editor.inputText("hello", clear = false),
        )
        verify(exactly = 1) { connection.commitText("hello", 1) }
    }

    @Test
    fun acceptedAppendWithThrowingPostState_isNotRetried() {
        val connection = mockk<InputConnection>(relaxed = true)
        var reads = 0
        every { connection.getExtractedText(any(), 0) } answers {
            if (reads++ == 0) {
                snapshot("", selectionStart = 0)
            } else {
                throw IllegalStateException("connection closed")
            }
        }
        every { connection.finishComposingText() } returns true
        every { connection.setSelection(0, 0) } returns true
        every { connection.commitText("hello", 1) } returns true
        val editor = editor { connection }

        assertEquals(
            TextInputResult.AcceptedUnverified,
            editor.inputText("hello", clear = false),
        )
        verify(exactly = 1) { connection.commitText("hello", 1) }
    }

    @Test
    fun clear_preservesAcceptanceWhenPostStateThrows() {
        val connection = mockk<InputConnection>(relaxed = true)
        var reads = 0
        every { connection.getExtractedText(any(), 0) } answers {
            if (reads++ == 0) {
                snapshot("old", selectionStart = 3)
            } else {
                throw IllegalStateException("connection closed")
            }
        }
        every { connection.getTextAfterCursor(any(), 0) } returns ""
        every { connection.finishComposingText() } returns true
        every { connection.setSelection(0, 3) } returns true
        every { connection.commitText("new", 1) } returns true
        val editor = editor { connection }

        assertEquals(
            TextInputResult.AcceptedUnverified,
            editor.inputText("new", clear = true),
        )
        verify(exactly = 1) { connection.commitText("new", 1) }
    }

    @Test
    fun appendWithoutReadableSnapshot_commitsOnceAtCurrentSelection() {
        val connection = mockk<InputConnection>(relaxed = true)
        every { connection.getExtractedText(any(), 0) } returns null
        every { connection.finishComposingText() } returns true
        every { connection.commitText("hello", 1) } returns true
        val editor = editor { connection }

        assertEquals(
            TextInputResult.AcceptedUnverified,
            editor.inputText("hello", clear = false),
        )
        verify(exactly = 0) { connection.setSelection(any(), any()) }
        verify(exactly = 1) { connection.commitText("hello", 1) }
    }

    @Test
    fun appendWithThrowingCommit_returnsUnknownWithoutRetry() {
        val connection = mockk<InputConnection>(relaxed = true)
        every { connection.finishComposingText() } returns true
        every { connection.getExtractedText(any(), 0) } returns null
        every { connection.commitText("hello", 1) } throws IllegalStateException("connection closed")
        val editor = editor { connection }

        assertEquals(
            TextInputResult.CommitOutcomeUnknown,
            editor.inputText("hello", clear = false),
        )
        verify(exactly = 1) { connection.commitText("hello", 1) }
    }

    @Test
    fun appendWithUnsupportedCompositionPreparation_commitsOnceAtCurrentSelection() {
        val connection = mockk<InputConnection>(relaxed = true)
        every { connection.getExtractedText(any(), 0) } returns snapshot("hello", selectionStart = 5)
        every { connection.finishComposingText() } returns false
        every { connection.commitText(" world", 1) } returns true
        val editor = editor { connection }

        assertEquals(
            TextInputResult.AcceptedUnverified,
            editor.inputText(" world", clear = false),
        )
        verify(exactly = 0) { connection.setSelection(any(), any()) }
        verify(exactly = 1) { connection.commitText(" world", 1) }
    }

    @Test
    fun clear_doesNotRetryAcceptedReplacementWithStaleReadback() {
        val connection = mockk<InputConnection>(relaxed = true)
        every { connection.getExtractedText(any(), 0) } returnsMany listOf(
            snapshot("x", selectionStart = 1),
            snapshot("x", selectionStart = 1),
        )
        every { connection.getTextAfterCursor(any(), 0) } returns ""
        every { connection.finishComposingText() } returns true
        every { connection.setSelection(0, 1) } returns true
        every { connection.commitText("long", 1) } returns true
        val editor = editor { connection }

        assertEquals(
            TextInputResult.AcceptedUnverified,
            editor.inputText("long", clear = true),
        )
        verify(exactly = 1) { connection.commitText("long", 1) }
    }

    @Test
    fun clear_reacquiresConnectionAfterRejectedCommit() {
        val stale = mockk<InputConnection>(relaxed = true)
        val fresh = mockk<InputConnection>(relaxed = true)
        every { stale.getExtractedText(any(), 0) } returns snapshot("old", selectionStart = 3)
        every { stale.getTextAfterCursor(any(), 0) } returns ""
        every { stale.finishComposingText() } returns true
        every { stale.setSelection(0, 3) } returns true
        every { stale.commitText("new", 1) } returns false
        every { fresh.getExtractedText(any(), 0) } returnsMany listOf(
            snapshot("old", selectionStart = 3),
            snapshot("\uFEFFnew", selectionStart = 4),
        )
        every { fresh.getTextAfterCursor(any(), 0) } returns ""
        every { fresh.finishComposingText() } returns true
        every { fresh.setSelection(0, 3) } returns true
        every { fresh.commitText("new", 1) } returns true

        val connections = ArrayDeque(listOf(stale, fresh, fresh))
        val editor = InputConnectionTextEditor(
            connectionProvider = { connections.removeFirstOrNull() },
            generationProvider = { 1L },
            sleep = {},
        )

        assertEquals(TextInputResult.Verified, editor.inputText("new", clear = true))
        verify(exactly = 1) { stale.commitText("new", 1) }
        verify(exactly = 1) { fresh.commitText("new", 1) }
    }

    @Test
    fun clear_doesNotRetryAfterInputSessionChanges() {
        val connection = mockk<InputConnection>(relaxed = true)
        every { connection.getExtractedText(any(), 0) } returns snapshot("old", selectionStart = 3)
        every { connection.getTextAfterCursor(any(), 0) } returns ""
        every { connection.finishComposingText() } returns true
        every { connection.setSelection(0, 3) } returns true
        every { connection.commitText("new", 1) } returns true
        var generation = 1L
        val editor = InputConnectionTextEditor(
            connectionProvider = { connection },
            generationProvider = { generation },
            sleep = { generation++ },
        )

        assertEquals(
            TextInputResult.InputSessionChanged,
            editor.inputText("new", clear = true),
        )
        verify(exactly = 1) { connection.commitText("new", 1) }
    }

    @Test
    fun rejectedOperationsRetryOnceThenFail() {
        val connection = mockk<InputConnection>(relaxed = true)
        every { connection.getExtractedText(any(), 0) } returns snapshot("old", selectionStart = 3)
        every { connection.getTextAfterCursor(any(), 0) } returns ""
        every { connection.finishComposingText() } returns true
        every { connection.setSelection(0, 3) } returns true
        every { connection.commitText("new", 1) } returns false
        val editor = editor { connection }

        assertEquals(TextInputResult.Rejected, editor.inputText("new", clear = true))
        verify(exactly = 2) { connection.commitText("new", 1) }
    }

    @Test
    fun clear_finishesCompositionBeforeSelectingEntireField() {
        val connection = mockk<InputConnection>(relaxed = true)
        every { connection.getExtractedText(any(), 0) } returnsMany listOf(
            snapshot("old", selectionStart = 3),
            snapshot("new", selectionStart = 3),
        )
        every { connection.getTextAfterCursor(any(), 0) } returns ""
        every { connection.finishComposingText() } returns true
        every { connection.setSelection(0, 3) } returns true
        every { connection.commitText("new", 1) } returns true
        val editor = editor { connection }

        assertEquals(TextInputResult.Verified, editor.inputText("new", clear = true))
        verifyOrder {
            connection.finishComposingText()
            connection.setSelection(0, 3)
            connection.commitText("new", 1)
        }
    }

    @Test
    fun clear_rejectsWhenCompositionCannotFinish() {
        val connection = mockk<InputConnection>(relaxed = true)
        every { connection.getExtractedText(any(), 0) } returns snapshot("old", selectionStart = 3)
        every { connection.finishComposingText() } returns false
        val editor = editor { connection }

        assertEquals(TextInputResult.Rejected, editor.inputText("new", clear = true))
        verify(exactly = 2) { connection.finishComposingText() }
        verify(exactly = 0) { connection.setSelection(any(), any()) }
        verify(exactly = 0) { connection.commitText(any(), any()) }
    }

    private fun editor(connectionProvider: () -> InputConnection?): InputConnectionTextEditor {
        return InputConnectionTextEditor(
            connectionProvider = connectionProvider,
            generationProvider = { 1L },
            sleep = {},
        )
    }

    private fun snapshot(
        text: String,
        startOffset: Int = 0,
        partialStartOffset: Int = -1,
        selectionStart: Int,
        selectionEnd: Int = selectionStart,
    ): ExtractedText {
        return ExtractedText().apply {
            this.text = text
            this.startOffset = startOffset
            this.partialStartOffset = partialStartOffset
            this.partialEndOffset = partialStartOffset
            this.selectionStart = selectionStart
            this.selectionEnd = selectionEnd
        }
    }
}
