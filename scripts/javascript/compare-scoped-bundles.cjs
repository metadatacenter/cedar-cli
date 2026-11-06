const fs = require('node:fs');
const acorn = require('acorn');
const scopes = require('eslint-scope');

function canonical(source) {
  const ast = acorn.parse(source, {ecmaVersion: 'latest', sourceType: 'module', ranges: true});
  const manager = scopes.analyze(ast, {ecmaVersion: 2022, sourceType: 'module', optimistic: false, ignoreEval: false});
  const protectedPositions = new Set();
  function walk(node) {
    if (!node || typeof node !== 'object') return;
    if (node.type === 'Property' && node.shorthand) protectedPositions.add(node.key.start);
    if (node.type === 'ExportSpecifier') protectedPositions.add(node.exported.start);
    if (node.type === 'ImportSpecifier' && node.imported.start === node.local.start) protectedPositions.add(node.imported.start);
    if (node.type === 'ExportNamedDeclaration' && node.declaration) {
      const declaration = node.declaration;
      if (declaration.id) protectedPositions.add(declaration.id.start);
      function protectPattern(pattern) {
        if (!pattern) return;
        if (pattern.type === 'Identifier') protectedPositions.add(pattern.start);
        else if (pattern.type === 'RestElement') protectPattern(pattern.argument);
        else if (pattern.type === 'AssignmentPattern') protectPattern(pattern.left);
        else if (pattern.type === 'ArrayPattern') pattern.elements.forEach(protectPattern);
        else if (pattern.type === 'ObjectPattern') pattern.properties.forEach(p => protectPattern(p.value || p.argument));
      }
      for (const variable of declaration.declarations || []) protectPattern(variable.id);
    }
    for (const value of Object.values(node)) {
      if (Array.isArray(value)) value.forEach(walk);
      else if (value && typeof value === 'object') walk(value);
    }
  }
  walk(ast);
  const replacements = new Map();
  const dynamic = [];
  const variables = manager.scopes.flatMap(scope => scope.variables);
  const ids = new Map(variables.map((variable, id) => [variable, id]));
  const parents = variables.map((_, id) => id);
  const find = id => parents[id] === id ? id : (parents[id] = find(parents[id]));
  // A class declaration binds the same written name both outside and inside its class scope.
  // Unify only variables whose declaration is literally the same identifier token.
  const declarations = new Map();
  variables.forEach((variable, id) => variable.identifiers.forEach(identifier => {
    const previous = declarations.get(identifier.start);
    if (previous !== undefined) parents[find(id)] = find(previous);
    else declarations.set(identifier.start, id);
  }));
  manager.scopes.forEach((scope, s) => {
    if (scope.directCallToEvalScope) dynamic.push(s);
    scope.variables.forEach((variable, v) => {
      for (const identifier of [...variable.identifiers, ...variable.references.map(r => r.identifier)]) {
        if (protectedPositions.has(identifier.start)) continue;
        const entry = {end: identifier.end, name: identifier.name, canonical: `<binding:${find(ids.get(variable))}>`};
        const previous = replacements.get(identifier.start);
        if (previous && previous.canonical !== entry.canonical) throw Error('Conflicting lexical binding');
        replacements.set(identifier.start, entry);
      }
    });
  });
  let cursor = 0; const parts = [];
  for (const [start, entry] of [...replacements].sort((a,b)=>a[0]-b[0])) {
    if (start < cursor) throw Error('Overlapping binding');
    parts.push(source.slice(cursor, start), entry.canonical);
    cursor = entry.end;
  }
  parts.push(source.slice(cursor));
  return {text: parts.join(''), variables: variables.map(variable => variable.name),
    scopeCount: manager.scopes.length, bindingOccurrences: replacements.size, directEvalScopes: dynamic};
}
try {
  const input = JSON.parse(fs.readFileSync(0, 'utf8'));
  const a = canonical(input.development), b = canonical(input.public);
  let equivalent = a.text === b.text && a.scopeCount === b.scopeCount
    && a.variables.length === b.variables.length && a.bindingOccurrences === b.bindingOccurrences;
  let renames = 0;
  for (let index = 0; index < a.variables.length; index++) {
    if (a.variables[index] === b.variables[index]) continue;
    renames++;
    if (!/^[A-Za-z_$][A-Za-z0-9_$]{0,2}$/.test(a.variables[index])
        || !/^[A-Za-z_$][A-Za-z0-9_$]{0,2}$/.test(b.variables[index] || '')) equivalent = false;
  }
  // Direct eval can observe a lexical name through a string; alpha-renaming is not a proof there.
  if (renames && (a.directEvalScopes.length || b.directEvalScopes.length)) equivalent = false;
  process.stdout.write(JSON.stringify({equivalent, renames}));
} catch (error) {
  process.stdout.write(JSON.stringify({equivalent: false, error: error.message}));
}
