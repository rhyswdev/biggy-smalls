/** Enter in a message box sends; Shift+Enter starts a new line, and Cmd or Ctrl+Enter still sends. The Enter that
 * ends an input method's composition (Japanese, Chinese, Korean) only ends it: Safari reports that one with
 * isComposing false, so its key code, 229, is checked too. */
export function sendKey(e: React.KeyboardEvent): boolean {
  return e.key === "Enter" && !e.shiftKey && !e.altKey && !e.nativeEvent.isComposing && e.nativeEvent.keyCode !== 229;
}
