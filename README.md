# Patch

[![HACS Badge](https://img.shields.io/badge/HACS-Default-31A9F4.svg?style=for-the-badge)](https://github.com/hacs/integration)

[![GitHub Release](https://img.shields.io/github/release/amitfin/patch.svg?style=for-the-badge&color=blue)](https://github.com/amitfin/patch/releases)

![Download](https://img.shields.io/github/downloads/amitfin/patch/total.svg?style=for-the-badge&color=blue) ![Analytics](https://img.shields.io/badge/dynamic/json?style=for-the-badge&color=blue&label=Analytics&suffix=%20Installs&cacheSeconds=15600&url=https://analytics.home-assistant.io/custom_integrations.json&query=$.patch.total)

![Project Maintenance](https://img.shields.io/badge/maintainer-Amit%20Finkelstein-blue.svg?style=for-the-badge)

## Patch Home Assistant Core files

**_Note: This is an advanced integration. If you are not a programmer, you probably don’t want to play with it._**

There are cases when a code change is slow to happen. For example, integrations have often dependency libraries which are maintained by a single person or a very small set of people. The pace of a change (e.g. code review, releasing a new version, etc') can take weeks. This integration mitigates such situations by allowing a short-term patch of the system. In addition, the patch will get re-applied after a Home Assistant update which brings a fresh container (overriding all patches).

_Note: When the change is inside an integration code, it's possible to override the entire integration using the instructions [here](https://developers.home-assistant.io/docs/development_tips/#test-core-integration-changes-in-your-production-home-assistant-environment). This is a better way for such situation. However, when the change is inside a dependency library, it's not straightforward to use the method above. This is a scenario where this integration can come handy._

## Configuration

`configuration.yaml` should be used (there is no UI on purpose). Here is an example:

```
patch:
  delay: 60
  restart: true
  files:
    - destination: "{site-packages}/pycoolmasternet_async/coolmasternet.py"
      base: https://raw.githubusercontent.com/OnFreund/pycoolmasternet-async/b463ac6101c25b027ecfb62c3d4edcc5bfbf4379/pycoolmasternet_async/coolmasternet.py
      patch: https://raw.githubusercontent.com/amitfin/pycoolmasternet-async/wait-for-prompt/pycoolmasternet_async/coolmasternet.py
    - name: adm_mapping.json
      destination: "{site-packages}/pysiaalarm/data"
      base: https://raw.githubusercontent.com/eavanvalkenburg/pysiaalarm/0df5af750412421e697a106aa5ac9dfec1727398/src/pysiaalarm/data
      patch: /share/fileserver/pysiaalarm/patch/data
```

`delay` is an optional integer parameter, with a default of 300 (seconds, which is 5 minutes). This is the delay between the startup time of the integration and when it applies the patches.

`restart` is an optional boolean parameter, with a default of `true`. If a patch was applied (to one file or more) and this parameter is `true` the integration initiates a restart of Home Assistant (Core). This should happen only once since the next time (after the restart) there should be no further patches.

`files` is a list of patches to apply. Each item on the list has the following properties:

- `name`: an optional file name. If it exists, it gets appended to the rest of the properties. If it doesn't exist, the rest of the properties should be supplied as a full path, including the file name.
- `destination`: the local path to the file which should get the patch.
- `base`: the path to the original file (before the patch). The patch happens only if the content of the file to be patched (`destination`) is identical to the content of the base file. Otherwise, a repair issue is raised. In such a case, a rebase of the patch is required along with updating the content of the files `base` and `patch`. This parameter can be provided as a local path or as a URL.
- `patch`: the path to the file with the change. It can be provided as a local path or as a URL.

All files must exist inside the Home Assistant Core environment. It’s convenient to point `base` and `patch` to [network shares](https://www.home-assistant.io/common-tasks/os#network-storage), or provide them as URLs.

The `destination` path can use the following variables as a prefix:

1. `site-packages`: path to the location of Python libraries (e.g. `/usr/local/lib/python3.14/site-packages`).
2. `homeassistant`: path to the `homeassistant` directory, i.e. `/usr/src/homeassistant/homeassistant` (the 2nd `/homeassistant` is not a mistake. There is `homeassistant` directory under the root.)

## Install

HACS is the preferred and easier way to install the component, and can be done by using this My button:

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=amitfin&repository=patch&category=integration)

Otherwise, download `patch.zip` from the [latest release](https://github.com/amitfin/patch/releases), extract and copy the content under `custom_components` directory.

Home Assistant restart is required once the integration files are copied (either by HACS or manually). After the restart, the `configuration.yaml` should be edited and the `patch` section should be created. An additional restart is required after that for the integration to be loaded.

## File System structure

Home Assistant can run in different configurations. A common one is Home Assistant Operating System, which will be used in the explanation below. In this configuration Home Assistant Core runs as a container. The 2 most relevant directories are:

1. `/usr/src/homeassistant`: this is the place with Home Assistant files built from the [Core repository](https://github.com/home-assistant/core). The variable `homeassistant` can be used as a prefix in the `destination` parameter and it will be resolved to `/usr/src/homeassistant/homeassistant` (the 2nd `/homeassistant` is not a mistake. There is `homeassistant` directory under the root.)
2. `/usr/local/lib/python3.14/site-packages`: this is the place where Python libraries are installed. (Note: `python3.14` will be changed when Home Assistant upgrades its Python version.) The variable `site-packages` can be used as a prefix in the `destination` parameter and it will be resolved automatically.

It’s possible to explore the environment along with the file system structure and content by:

1. SSH-ing into the host. Instructions are [here](https://developers.home-assistant.io/docs/operating-system/debugging/).
2. In the SSH session switch into Home Assistant Core’s container via the command: `docker exec -it homeassistant /bin/bash`

## Reload

The integration also exposes a `reload` action. The delay parameter is ignored in this case and the logic is executed immediately, including Home Assistant restart, when needed.

## Re-patching

It's not possible to re-patch a file by simply updating the content of `patch`. The problem is that `destination` was already patched, so it's different than `base` and therefore will not be patched again. The solution is to change `base` to be the same as `destination`. This will cause the comparison to succeed as both will be pointing the same file. Once the patch is applied, `base` should get reverted to it's original value, so the patch can be safely re-applied on Home Assistant update (only if the file is still identical to base.)

## Uninstall

1. **Delete the configuration:**
   - Delete the `patch:` section from `configuration.yaml`.

2. **Remove the integration files:**
   - If the integration was installed via **HACS**, follow the [official HACS removal instructions](https://www.hacs.xyz/docs/use/repositories/dashboard/#removing-a-repository).
   - Otherwise, manually delete the integration’s folder `custom_components/patch`.

📌 A **Home Assistant core restart** is required to fully apply the removal.

## Contributions are welcome!

If you want to contribute to this please read the [Contribution guidelines](CONTRIBUTING.md)
