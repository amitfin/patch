# Patch

[![HACS Badge](https://img.shields.io/badge/HACS-Default-31A9F4.svg?style=for-the-badge)](https://github.com/hacs/integration)

[![GitHub Release](https://img.shields.io/github/release/amitfin/patch.svg?style=for-the-badge&color=blue)](https://github.com/amitfin/patch/releases)

![Download](https://img.shields.io/github/downloads/amitfin/patch/total.svg?style=for-the-badge&color=blue) ![Analytics](https://img.shields.io/badge/dynamic/json?style=for-the-badge&color=blue&label=Analytics&suffix=%20Installs&cacheSeconds=15600&url=https://analytics.home-assistant.io/custom_integrations.json&query=$.patch.total)

![Project Maintenance](https://img.shields.io/badge/maintainer-Amit%20Finkelstein-blue.svg?style=for-the-badge)

## Patch Home Assistant core files
***Note: This is an advanced integration. If you are not a programmer, you probably don’t want to play with it.***

There are cases when a code change is slow to happen. For example, integrations have often dependency libraries which are maintained by a single person or a very small set of people. The pace of a change (e.g. code review, releasing a new version, etc') can take weeks. This integration mitigates such situations by allowing a short-term patch of the system. In addition, the patch will get re-applied after a Home Assistant update which brings a fresh container (overriding all patches).

## Configuration
`configuration.yaml` should be used (there is no UI on purpose). Here is an example:
```
patch:
  delay: 60
  files:
    - name: adm_mapping.json
      base: /share/fileserver/pysiaalarm/base/data
      destination: /usr/local/lib/python3.11/site-packages/pysiaalarm/data
      patch: /share/fileserver/pysiaalarm/patch/data
```

`delay` is an optional parameter, with a default of 300 seconds (5 minutes). This is the delay between the startup time of the integration and when it applies the patches.

If a patch was applied (to one file or more) the integration initiates a restart of Home Assistant (core). This should happen only once since the next time (after the restart) there should be no further patches.

`files` is a list of patches to apply. It has the following properties (all are mandatory):
- `name`: the file name
- `base`: the directory containing an original copy of the file (before the patch). The patch happens only if the content of the file to be patched is identical to the base file.
- `destination`: the local directory with the file to be patched.
- `patch`: the directory containing the file with the change.

All files must exist (e.g. `base/name`, etc') inside the Home Assistant core environment. It’s convenient to mount `base` and `patch` directories as [network shares](https://www.home-assistant.io/common-tasks/os#network-storage).

## File System structure
Home Assistant can run in different configuration. A common one is Home Assistant Operating System, which will be used in the explaination below. In this configuration Home Assistant Core runs as a container. The 2 most relevant directories are:
1)	`/usr/src/homeassistant`: this is the place with Home Assistant files built from the [core repository]( https://github.com/home-assistant/core).
2)	`/usr/local/lib/python3.11/site-packages`: this is the place where Python libraries are installed. (Note: `python3.11` will be changed when Home Assistatnt upgrades its Python version.)

It’s possible to explore the environment along with the file system structure and content by:
1)	SSH-ing into the host. Instructions are [here](https://developers.home-assistant.io/docs/operating-system/debugging/).
2)	In the SSH session switch into Home Assistant core’s container via the command: `docker exec -it homeassistant /bin/bash`

## Reload
The integration also exposes a `reload` custom service. The delay parameter is ignored in this case and the logic is executed immediately, including Home Assistant restart, when needed.

## Re-patching
It's not possible to re-patch a file by simply updating the contant of `patch/name`. The problem is that `destination/name` was already patched, so it's different than `base/name` and therefore will not be patched again. The solution is to change `base` to be the same as `destination`. This will cause the comparison to succeed as both will be pointing the same file. Once the patch is applied, `base` should get reverted to it's original value, so the patch can be safely re-applied on Home Assistant update (only if the file is still identical to base.)
