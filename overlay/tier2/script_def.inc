// ro-admin Tier 2 hook -- paste into rAthena's src/custom/script_def.inc
// https://github.com/RafaDyck/ro-admin
//
// "" is the zero-argument signature. BUILDIN_DEF(x,args) expands to
// { buildin_ ## x , #x , args, nullptr } (src/map/script.cpp:4911); rAthena's
// own zero-argument commands use the same empty string, e.g.
// BUILDIN_DEF(detachrid,"") at src/map/script.cpp:28039 (commit ad04a42).
BUILDIN_DEF(ro_admin_sync, ""),
