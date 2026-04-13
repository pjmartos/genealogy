### Layer ordering

Every reachable prompt is placed at its BFS distance from the root. Ties at the same distance are broken by BFS enqueue order (child sequence order in the enqueuing prompt's `ancestors`). The root is distance 0 (wins everything).

### Map deep-merge with nearest-wins at leaves

```yaml
# ancestor (distance 1)
database:
  host: "base.internal"
  port: 5432
```

```yaml
# root (distance 0)
database:
  host: "override.internal"
  ssl: true
```

**Resolved:**
```yaml
database:
  host: "override.internal"   # nearer wins
  ssl: true                   # only root provides
  port: 5432                  # only ancestor provides; survives
```

### Lists replace wholesale

```yaml
# ancestor
items: [a, b, c]
```
```yaml
# root
items: [x, y]
```
```yaml
# resolved
items: [x, y]
```

### `null` shadows maps wholesale

```yaml
# ancestor
a: { b: 1, c: 2 }
```
```yaml
# root
a: null
```
```yaml
# resolved
a: null
```
`${a}` aborts with exit `14` `explicit_null`; `${a.b}` aborts with exit `14` `not_provided` (walk hits null at a non-final segment).

### Placeholder forms

| Form                       | Meaning                                                                                                    |
|----------------------------|------------------------------------------------------------------------------------------------------------|
| `${foo.bar}`               | Lookup. Structural (replaces whole node) when the scalar is *exactly* this placeholder; textual otherwise. |
| `${=foo.bar}`              | Non-splatting structural form. Inserts a list as a single nested element instead of splicing.              |
| `$${foo.bar}`              | Literal `${foo.bar}` in the output.                                                                        |
| Block scalars (`\|` / `>`) | Always textual, never structural.                                                                          |

### List splat

A structural placeholder inside a YAML sequence whose value resolves to a list is spliced in place:

```yaml
# ancestor
items: [one, two, three]
```
```yaml
# root
list:
  - head
  - ${items}         # splat
  - tail
```
```yaml
# resolved
list: [head, one, two, three, tail]
```

### Non-splat form

```yaml
list:
  - ${=items}         # inserted as one nested list
```
```yaml
list: [[one, two, three]]
```

### Textual interpolation

```yaml
body: "Running in ${vars.region} at ${vars.port}"
```
Resolved value must be a scalar; a list/map in textual position aborts with exit `15` (`non_scalar_in_textual`).

### Recursive resolution

Resolved values are themselves interpolated. A lookup that returns `"${b}"` is re-entered against the same root namespace, so chains and nested placeholders inside resolved maps/lists expand fully:

```yaml
# vars
a: "${b}"
b: [1, 2, 3]
```
```yaml
# root
xs:
  - ${a}
  - tail
```
```yaml
# resolved
xs: [1, 2, 3, tail]
```

`${a}` resolves to `"${b}"`, which resolves to `[1, 2, 3]`; the outer list context still splats. The same applies to placeholders found inside resolved maps and list elements. To emit a literal `${...}` that should not recurse, escape it as `$${...}`.

### Cycles

A placeholder path that reappears while resolving itself aborts with exit `12` (`cycle_detected`). The cycle is reported as the chain of paths from the first reference back to the repeat:

```yaml
a: "${b}"
b: "${a}"
# referencing ${a} aborts with cycle: [a, b, a]
```

Self-references (`a: "${a}"`) and cycles via map/list values (`a: { loop: "${a}" }`) are detected the same way.

### Version conflicts

When the same package is reached at multiple versions, the nearest (then earliest enqueued) version wins. Losing versions are silently superseded; all references are redirected to the winner. If the winner does not contain a referenced prompt `id`, the tool aborts with exit `11`.
