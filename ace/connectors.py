"""Connectors for outside channels. v1 ships honest stubs: they report their state and do nothing else."""

CONNECTORS = {
    "photon": {
        "configured": False,
        "note": "iMessage texting through Photon is a stated requirement but is not built in v1. "
                "What 'Photon' refers to is still to be confirmed, so no integration was attempted.",
    },
}


def status():
    return {k: dict(v) for k, v in CONNECTORS.items()}
