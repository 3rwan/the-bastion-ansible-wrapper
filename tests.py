import os
import sys
from unittest.mock import patch, MagicMock

from yaml import dump

from lib import (
    awx_get_inventory_file,
    get_bastion_vars,
    get_var_within,
    manage_conf_file,
    run_ssh_command,
)

BASTION_HOST = "my_bastion"
BASTION_PORT = 22
BASTION_USER = "my_bastion_user"
BASTION_CONF_FILE = "/tmp/test_bastion_conf_file.yml"


def test_manage_conf_file_bastion_host_undefined():
    bastion_host, bastion_port, bastion_user = manage_conf_file(
        BASTION_CONF_FILE, None, BASTION_PORT, BASTION_USER
    )
    assert bastion_host == BASTION_HOST


def test_manage_conf_file_bastion_port_undefined():
    bastion_host, bastion_port, bastion_user = manage_conf_file(
        BASTION_CONF_FILE, BASTION_HOST, None, BASTION_USER
    )
    assert bastion_port == BASTION_PORT


def test_manage_conf_file_bastion_user_undefined():
    bastion_host, bastion_port, bastion_user = manage_conf_file(
        BASTION_CONF_FILE, BASTION_HOST, BASTION_PORT, None
    )
    assert bastion_user == BASTION_USER


def test_manage_conf_file_bastion_all_undefined():
    write_conf_file(BASTION_CONF_FILE)
    bastion_host, bastion_port, bastion_user = manage_conf_file(
        BASTION_CONF_FILE, None, None, None
    )
    assert bastion_user == BASTION_USER
    assert bastion_port == BASTION_PORT
    assert bastion_host == BASTION_HOST


def write_conf_file(conf_file):
    with open(conf_file, "w") as f:

        data = {
            "bastion_host": BASTION_HOST,
            "bastion_port": BASTION_PORT,
            "bastion_user": BASTION_USER,
        }

        dump(data, f)


write_conf_file(BASTION_CONF_FILE)


def test_get_var_within_one_level():
    hostvars = {"bastion_host": "{{ bastion_fqdn }}", "bastion_fqdn": "my_real_bastion"}
    bastion_host = get_var_within(hostvars["bastion_host"], hostvars)
    assert bastion_host == hostvars["bastion_fqdn"]


def test_get_var_within_two_levels():
    hostvars = {
        "bastion_host": "{{ bastion_fqdn }}",
        "bastion_fqdn": "{{ my_other_var }}",
        "my_other_var": "my_real_bastion",
    }
    bastion_host = get_var_within(hostvars["bastion_host"], hostvars)
    assert bastion_host == hostvars["my_other_var"]


def test_get_var_within_not_found():
    hostvars = {"bastion_host": "{{ bastion_fqdn }}"}
    bastion_host = get_var_within(hostvars["bastion_host"], hostvars)
    assert not bastion_host


def test_get_var_within_infinite():
    hostvars = {
        "bastion_host": "{{ bastion_fqdn }}",
        "bastion_fqdn": "{{ bastion_host }}",
    }
    bastion_host = get_var_within(hostvars["bastion_host"], hostvars)
    assert not bastion_host


def test_get_var_not_a_jinja2_var():
    hostvars = {"bastion_host": "{{ bastion_fqdn"}
    bastion_host = get_var_within(hostvars["bastion_host"], hostvars)
    assert bastion_host == hostvars["bastion_host"]


def test_get_var_not_a_string():
    hostvars = {"bastion_host": 68}
    bastion_host = get_var_within(hostvars["bastion_host"], hostvars)
    assert bastion_host == hostvars["bastion_host"]


def test_awx_get_inventory_file_default():
    assert awx_get_inventory_file() == "/runner/inventory/hosts"


def test_awx_get_inventory_file_env_defined():
    env_path = "/my_awx"
    os.environ["AWX_RUN_DIR"] = env_path
    assert awx_get_inventory_file() == f"{env_path}/inventory/hosts"
    os.environ.pop("AWX_RUN_DIR")


def test_get_bastion_vars():
    host_vars = {
        "bastion_port": BASTION_PORT,
        "bastion_host": BASTION_HOST,
        "bastion_user": BASTION_USER,
    }
    bastion_vars = get_bastion_vars(host_vars)
    assert (
        bastion_vars["bastion_port"] == BASTION_PORT
        and bastion_vars["bastion_host"] == BASTION_HOST
        and bastion_vars["bastion_user"] == BASTION_USER
    )


def test_get_bastion_vars_not_full():
    host_vars = {"bastion_port": BASTION_PORT, "bastion_user": BASTION_USER}
    bastion_vars = get_bastion_vars(host_vars)
    assert not bastion_vars["bastion_host"]


# --- run_ssh_command tests ---


@patch("lib.find_executable", return_value="/usr/bin/ssh")
@patch("lib.subprocess.run")
def test_run_ssh_command_success(mock_run, mock_find):
    """Successful SSH command: exit 0, no diagnostic emitted."""
    mock_run.return_value = MagicMock(returncode=0)
    with patch("lib.sys.exit") as mock_exit:
        run_ssh_command(["ssh", "-p", "22", "bastion.example.com"])
    mock_exit.assert_called_once_with(0)


@patch("lib.find_executable", return_value="/usr/bin/ssh")
@patch("lib.subprocess.run")
def test_run_ssh_command_exit_255_emits_diagnostic(mock_run, mock_find):
    """Exit 255: bastion diagnostic appended to stderr."""
    mock_run.return_value = MagicMock(returncode=255)
    with patch("lib.sys.exit") as mock_exit, patch("lib.sys.stderr") as mock_stderr:
        run_ssh_command(["ssh", "-p", "22", "bastion.example.com"])
    written = "".join(call.args[0] for call in mock_stderr.write.call_args_list)
    assert "[BASTION ERROR]" in written
    assert "exit code 255" in written
    mock_exit.assert_called_once_with(255)


@patch("lib.find_executable", return_value="/usr/bin/ssh")
@patch("lib.subprocess.run")
def test_run_ssh_command_nonzero_not_255_no_diagnostic(mock_run, mock_find):
    """Non-zero exit code that is NOT 255: no bastion diagnostic emitted."""
    mock_run.return_value = MagicMock(returncode=1)
    with patch("lib.sys.exit") as mock_exit, patch("lib.sys.stderr") as mock_stderr:
        run_ssh_command(["ssh", "-p", "22", "bastion.example.com"])
    mock_stderr.write.assert_not_called()
    mock_exit.assert_called_once_with(1)


@patch("lib.find_executable", return_value="/usr/bin/ssh")
@patch("lib.subprocess.run")
def test_run_ssh_command_stderr_passed_directly(mock_run, mock_find):
    """stderr=sys.stderr is passed to subprocess.run (direct passthrough)."""
    mock_run.return_value = MagicMock(returncode=0)
    with patch("lib.sys.exit"):
        run_ssh_command(["ssh", "-p", "22", "bastion.example.com"])
    call_kwargs = mock_run.call_args[1]
    assert call_kwargs["stderr"] is sys.stderr


@patch("lib.find_executable", return_value=None)
def test_run_ssh_command_ssh_not_found(mock_find):
    """ssh not in PATH: error message and exit 1."""
    with patch("lib.sys.exit", side_effect=SystemExit(1)) as mock_exit, \
         patch("lib.sys.stderr") as mock_stderr:
        try:
            run_ssh_command(["ssh", "-p", "22", "bastion.example.com"])
        except SystemExit:
            pass
    written = "".join(call.args[0] for call in mock_stderr.write.call_args_list)
    assert "Could not find" in written
    mock_exit.assert_called_once_with(1)


@patch("lib.find_executable", return_value="/usr/bin/ssh")
@patch("lib.subprocess.run")
def test_run_ssh_command_integer_args_sanitized(mock_run, mock_find):
    """Integer arguments (e.g. port) are converted to strings without error."""
    mock_run.return_value = MagicMock(returncode=0)
    with patch("lib.sys.exit"):
        run_ssh_command(["ssh", "-p", 22, "bastion.example.com"])
    called_args = mock_run.call_args[0][0]
    assert all(isinstance(a, str) for a in called_args)
