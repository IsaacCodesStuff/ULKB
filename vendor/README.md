# vendor/

Code that is not part of the Linux kernel tree itself but gets built into it or
alongside it. This is the equivalent of `vendor/` in the Samsung kernel tree.

Put things here such as:

- scheduler patch sets (REM, ICS), one folder per series, for example
  `vendor/rem/0001-sched-add-memory-pressure-signal.patch`
- out-of-tree kernel modules

A build file picks patches up with the `patches:` list. A folder applies every
`*.patch` file inside it, in name order:

```yaml
patches:
  - vendor/rem
```

Patches are applied to the pristine source folder (`kernel-<version>/`) before
configuring, and each one is committed there so `git log` shows what was applied.
