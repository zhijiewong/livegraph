; Reference query documenting the Python node shapes livegraph extracts.
; The extractor walks the tree directly (it needs parent context), but
; these patterns describe the captured constructs.

(function_definition name: (identifier) @function.name) @function.def
(class_definition name: (identifier) @class.name) @class.def
(import_statement) @import
(import_from_statement) @import.from
(call function: (_) @call.callee) @call
