# Host-native Documents and Downloads

The host user's directories are authoritative. Fernando bind-mounts them into `/home/kasm-user/Documents` and `/home/kasm-user/Downloads`. The desktop profile remains in `data/desktop`; browser profiles, credentials, and other private desktop state are separate from these shared directories.

Setup does not remove or replace existing Documents, Downloads, or Desktop directories, and does not recursively change ownership of the user's home directory. It creates missing Documents/Downloads only during explicit setup. Existing user-managed symlinks to custom storage are respected. Existing native-directory ownership is preserved.

## Path configuration

`scripts/desktop-shares.py` resolves directories for the account running Fernando:

1. Conventional `~/Documents` and `~/Downloads` paths.
2. Linux XDG `user-dirs.dirs`, honoring `XDG_CONFIG_HOME` and `$HOME` expansion without executing shell configuration.
3. Optional `data/shared-directories.json`, with absolute `Documents` and/or `Downloads` paths.
4. Explicit `FERNANDO_DOCUMENTS_DIR` / `FERNANDO_DOWNLOADS_DIR` environment overrides.

The home directory itself and its ancestors are rejected as share roots. Missing or invalid directories fail startup rather than letting Docker silently create root-owned substitutes. Source paths are resolved on the host and dollar signs are escaped for Compose interpolation.

`scripts/desktop-compose.sh` generates `data/shared-directories.compose.json` and combines it with the selected main Compose file and any local `docker-compose.override.yml`. The generated file is machine-specific runtime state, not repository configuration. Start, stop, desktop rebuild/update, and both setup entry points use this wrapper. Use it instead of invoking the base Compose file alone when managing the desktop.

Linux setup permits `FERNANDO_USER` and `FERNANDO_INSTALL_DIR` overrides and uses an existing account's actual home directory. Share preparation runs as that account, with its home selected explicitly through sudo's `-H` option.

## Permissions and restart behavior

Sysbox user-namespace isolation remains enabled. On Linux, the desktop image includes ACL tools and an ordered `fernando-shared-folders.service`. It runs after the base root initialization and before `kasm.service`; Kasm requires it to succeed.

The helper grants the host service UID and the desktop account UID access to the two explicit shared mounts. Existing regular files/directories receive scoped access ACLs on initial provisioning; directories receive default ACLs for normal future file/directory creation. Symlinks inside the shared trees are not followed. Other principals' existing effective ACL permissions are preserved when expanding the ACL mask. Ownership and world-access permissions of the host's directories are not rewritten.

The private desktop profile records the provisioned roots' inode/device identities and participating UIDs. Ordinary restarts/mutates reassert only the two root policies: they do not recursively traverse users' entire Documents/Downloads trees. A new share or changed identity gets an initial pass. Explicit restrictive file modes applied later, such as chmod 600, are respected; startup does not continually override them.

Kasm's initial profile-copy operation excludes Documents and Downloads and does not overwrite existing profile entries. It cannot populate or reset the user's shared folders from an image default profile.

The macOS Compose entrypoint runs the same initialization boundary before Kasm. Docker Desktop's host-sharing provider handles native host access; Linux POSIX ACL rewriting is not applied to macOS user data. Desktop-account read/write/traverse access is checked explicitly. Linux/Sysbox has been runtime-tested; Docker Desktop/macOS changes require verification on a Mac.

## Explicit legacy migration

Old installations may have `~/Documents` and `~/Downloads` symlinked into `data/desktop`. Normal startup/setup refuses that legacy arrangement rather than silently migrating personal data.

Migration is deliberate:

1. Build the updated image before stopping the desktop.
2. Grant the host migration account access to the two legacy directories if needed. Cross-parent directory renames require write permission on the directories themselves as well as their parents. Use the scoped shared-access helper, not a recursive home-wide chown.
3. Stop `fernando-desktop` so desktop applications cannot write during relocation.
4. Run `python3 scripts/desktop-shares.py migrate` as the host Fernando account.
5. Recreate the desktop through `bash scripts/desktop-compose.sh up -d fernando-desktop`, retaining its VNC password environment.

The migration recognizes only the exact legacy application symlink targets. It retains the old links as `.Documents.fernando-legacy-link` / `.Downloads.fernando-legacy-link`, and renames the backing directories into the native host locations on the same filesystem. File contents and inodes are preserved; no merging, copying over existing directories, or recursive deletion occurs. It checks access before moving links, refuses pre-existing backup collisions, and can resume after the link-backup stage. Cross-filesystem migration requires a separate explicit copy-and-verify procedure.

The retained links are rollback metadata, not duplicate backups of file contents. Once migrated, the former profile paths are container mountpoints; normal data access uses the host-native paths.

## Verification

`./venv/bin/python -m unittest discover -s tests -p test_desktop_shares.py`

Checks cover existing-data/ownership preservation, inode-preserving legacy migration, XDG/custom symlinks, effective ACL restrictions and idempotence, and non-overwriting default-profile initialization.

This installation additionally passed real Sysbox tests of host-to-desktop and desktop-to-host editing, inherited permissions on new files/subdirectories, repeated initialization without resetting restrictive child permissions, unchanged native directory ownership, and the live Files API create-in-Downloads/move-to-Documents operation.
