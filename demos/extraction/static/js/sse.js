// sse.js — extracted from compare.html (Wave D — JS carve)
// Behavior-preserving line-range extraction. Top-level let/const
// share global lexical scope across <script> tags. Function decls
// at top-level become window properties (used by inline on*= attrs).

// --- compare.html lines 2480-2483: eventSource decl ---
// ============================================================
// State
// ============================================================
let eventSource = null;

