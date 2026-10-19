/**
 * Bundled skill provider for dsh-abaqus-local-simulation.
 *
 * Registers one skill, `abaqus-local-simulation`, whose body is
 * skills/abaqus-local-simulation/SKILL.md plus sibling Python scripts,
 * references and assets. DSH's skill catalog lists it and loads the body on
 * demand; the agent then runs the Python CLI / local stdio MCP described there.
 */
import { readFileSync, statSync } from "node:fs";
import { readFile } from "node:fs/promises";
import { dirname, isAbsolute, join, sep } from "node:path";
import { fileURLToPath } from "node:url";

/** Single skill name bundled by this provider. */
const SKILL_NAME = "abaqus-local-simulation";

/**
 * Minimal YAML frontmatter parser for a SKILL.md. The harness only needs the
 * `name`, `description` and `whenToUse` scalar fields; full YAML parsing would
 * pull in a dependency this bundle should not require.
 */
function parseFrontmatter(raw) {
  const match = /^---\r?\n([\s\S]*?)\r?\n---(?:\r?\n|$)/u.exec(raw);
  if (match?.[1] === undefined) throw new Error(`abaqus-local-simulation: SKILL.md has no YAML frontmatter`);
  const fields = {};
  for (const line of match[1].split(/\r?\n/)) {
    const kv = /^([A-Za-z0-9_-]+):\s?(.*)$/.exec(line);
    if (kv) fields[kv[1]] = kv[2];
  }
  return { fields, body: raw.slice(match[0].length).trim() };
}

/** Cordis plugin identity. */
const name = "abaqus-local-simulation";
/** Registry used by the bundled provider. */
const inject = ["skills"];

/** Parse a SKILL.md file into { description, whenToUse, content }. */
function parseSkill(raw, skillPath) {
  const { fields, body } = parseFrontmatter(raw);
  const description = fields.description;
  if (typeof description !== "string" || description.length === 0)
    throw new Error(`abaqus-local-simulation: ${skillPath} has no description`);
  const whenToUse = fields.whenToUse;
  return {
    description,
    whenToUse: typeof whenToUse === "string" && whenToUse.length > 0 ? whenToUse : undefined,
    content: body
  };
}

/**
 * Register the abaqus-local-simulation skill onto the bundled skill provider.
 * @param ctx - Context carrying the skill registry.
 */
function apply(ctx) {
  const assetRoot = dirname(fileURLToPath(import.meta.url));
  const directory = assetRoot;
  const skillPath = join(directory, "SKILL.md");
  if (!isAbsolute(skillPath) || !statSync(skillPath).isFile())
    throw new Error(`abaqus-local-simulation: expected SKILL.md at ${skillPath}`);
  const parsed = parseSkill(readFileSync(skillPath, "utf8"), skillPath);
  const candidate = {
    name: SKILL_NAME,
    description: parsed.description,
    ...(parsed.whenToUse !== undefined ? { whenToUse: parsed.whenToUse } : {}),
    invocation: { modelInvocable: true, userInvocable: true },
    provider: "abaqus-local-simulation",
    source: "bundled",
    rank: 600,
    resourceBase: { kind: "directory", path: directory.split(sep).join("/") },
    locator: skillPath
  };
  const provider = {
    name: "abaqus-local-simulation",
    list: () => Promise.resolve([candidate]),
    async get(found, options) {
      const { rank: _rank, locator, ...summary } = found;
      const content = parseSkill(await readFile(locator, { encoding: "utf8", signal: options.signal }), locator).content;
      return { ...summary, content };
    }
  };
  ctx.skills.registerProvider(() => provider);
}

export { apply, inject, name };
