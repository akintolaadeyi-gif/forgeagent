import os

import pytest


@pytest.mark.skipif(os.name == "nt", reason="POSIX system directories")
@pytest.mark.parametrize("bad", ["/etc", "/usr", "/var", "/opt", "/System", "/Library", "/Applications"])
def test_system_directories_rejected_as_workspace(bad):
    from fastapi import HTTPException

    from app.api.routes import validate_workspace

    with pytest.raises(HTTPException) as e:
        validate_workspace(bad)
    assert e.value.status_code == 400
