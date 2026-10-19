/**
 * Cordis host plugin entry for dsh-abaqus-local-simulation.
 *
 * This plugin carries a bundled skill (skills/abaqus-local-simulation/SKILL.md)
 * whose backend is a Python CLI / local stdio MCP. The Python code is executed
 * by the agent through the shell tool, so this host entry only needs to register
 * the skill provider so DSH's skill catalog discovers it. No Node services,
 * web UI, or native commands are introduced.
 *
 * `name` and `inject` are exported statically so the loader accepts the bundle
 * before `apply` runs.
 */
export { name, inject, apply } from "../skills/abaqus-local-simulation/host-plugin.js";
