from ape import plugins

from ape_sourcify.config import SourcifyConfig
from ape_sourcify.dependency import SourcifyDependency
from ape_sourcify.explorer import Sourcify
from ape_sourcify.networks import NETWORKS


@plugins.register(plugins.ExplorerPlugin)
def explorers():
    for ecosystem_name, network_names in NETWORKS.items():
        for network_name in network_names:
            yield ecosystem_name, network_name, Sourcify
            yield ecosystem_name, f"{network_name}-fork", Sourcify


@plugins.register(plugins.Config)
def config_class():
    return SourcifyConfig


@plugins.register(plugins.DependencyPlugin)
def dependencies():
    yield "sourcify", SourcifyDependency


__all__ = [
    "NETWORKS",
    "Sourcify",
    "SourcifyConfig",
    "SourcifyDependency",
]
