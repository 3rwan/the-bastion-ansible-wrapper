import json
import logging
import os
import subprocess
import sys
import time

from yaml import YAMLError, safe_load

# Known SSH/Bastion error patterns and their user-friendly diagnostics.
# Each entry maps a substring found in stderr to a contextual error message.
BASTION_ERROR_PATTERNS = {
    "Permission denied": (
        "[BASTION ERROR] SSH connection to the Bastion was denied.\n"
        "Verify that your SSH key is authorized on the Bastion, "
        "and that bastion_user and bastion_host are correct.\n"
    ),
    "Connection refused": (
        "[BASTION ERROR] Connection to the Bastion was refused.\n"
        "Verify that the Bastion host and port are correct "
        "and that the Bastion is accepting connections.\n"
    ),
    "Could not resolve hostname": (
        "[BASTION ERROR] Could not resolve the Bastion hostname.\n"
        "Verify that bastion_host is set to a valid, resolvable hostname.\n"
    ),
    "Connection timed out": (
        "[BASTION ERROR] Connection to the Bastion timed out.\n"
        "Verify network connectivity to the Bastion host and port.\n"
    ),
    "Operation timed out": (
        "[BASTION ERROR] Connection to the Bastion timed out.\n"
        "Verify network connectivity to the Bastion host and port.\n"
    ),
    "No route to host": (
        "[BASTION ERROR] No route to the Bastion host.\n"
        "Verify network connectivity and that bastion_host is correct.\n"
    ),
}


def find_executable(executable, path=None):
    """Find the absolute path of an executable

    :return: path
    :rtype: str
    """
    _, ext = os.path.splitext(executable)

    if os.path.isfile(executable):
        return executable

    if path is None:
        path = os.environ.get("PATH", os.defpath)

    for p in path.split(os.pathsep):
        f = os.path.join(p, executable)
        if os.path.isfile(f):
            return f


def get_inventory():
    """Fetch ansible-inventory --list

    :return: inventory
    :rtype: dict
    """
    inventory_cmd = find_executable("ansible-inventory")
    if not inventory_cmd:
        raise Exception("Failed to identify path of ansible-inventory")

    inventory = None

    # read and invalidate the inventory cache file
    cache_file = os.environ.get("BASTION_ANSIBLE_INV_CACHE_FILE")
    if cache_file:
        cache = get_inventory_from_cache(
            cache_file=cache_file,
            cache_timeout=int(os.environ.get("BASTION_ANSIBLE_INV_CACHE_TIMEOUT", 60)),
        )
        if cache:
            inventory = cache.get("inventory")

    if inventory:
        return inventory

    # ex : export BASTION_ANSIBLE_INV_OPTIONS="-i my_inventory -i my_second_inventory"
    inventory_options = os.environ.get("BASTION_ANSIBLE_INV_OPTIONS", "")

    command = "{} {} --list".format(inventory_cmd, inventory_options)
    inventory = get_inv_from_command(command)
    if cache_file:
        write_inventory_to_cache(cache_file=cache_file, inventory=inventory)

    return inventory


def get_inventory_from_cache(cache_file, cache_timeout):
    """Read ansible-inventory from cache file

    :return: Inventory cache with `updated_at` (to expire the cache) and
        `inventory` (results of `ansible-inventory` command) keys.
    :rtype: dict
    """
    try:
        # Load JSON from cache file
        with open(cache_file, "r") as fd:
            cache = json.load(fd)
    except IOError:
        # File does not exist or path is incorrect
        return None
    except:
        # Invalid JSON or any other error
        pass
    else:
        # Check cache expiry
        if cache.get("updated_at", 0) >= int(time.time()) - cache_timeout:
            return cache

    # Cache expired or any other error
    try:
        os.remove(cache_file)
    except:
        pass

    return None


def write_inventory_to_cache(cache_file, inventory):
    """Write inventory with last update time to a cache file"""
    cache = {"inventory": inventory, "updated_at": int(time.time())}
    with open(cache_file, "w") as fd:
        json.dump(cache, fd)


def get_hostvars(host) -> dict:
    """Fetch hostvars for the given host

    Ansible either uses the "ansible_host" inventory variable or the hostname.
    Fetch inventory and browse all hostvars to return only the ones for the host.

    :return: hostvars
    :rtype: dict
    """
    inventory = get_inventory()
    all_hostvars = inventory.get("_meta", {}).get("hostvars", {})
    for inventory_host, hostvars in all_hostvars.items():
        if inventory_host == host or hostvars.get("ansible_host") == host:
            return hostvars
    # Host not found
    return {}


def manage_conf_file(conf_file, bastion_host, bastion_port, bastion_user):
    """Fetch the bastion vars from a config file.

    There will be set if not already defined, and before looking in the ansible inventory

    """

    if os.path.exists(conf_file):
        try:
            with open(conf_file, "r") as f:
                yaml_conf = safe_load(f)

                if not bastion_host:
                    bastion_host = yaml_conf.get("bastion_host")
                if not bastion_port:
                    bastion_port = yaml_conf.get("bastion_port")
                if not bastion_user:
                    bastion_user = yaml_conf.get("bastion_user")

        except (YAMLError, IOError) as e:
            print("Error loading yaml file: {}".format(e))

    return bastion_host, bastion_port, bastion_user


def get_var_within(my_value, hostvar, check_list=None):
    """If a value is a jinja2 var, try to resolve it in the hostvars

    Ex:
        "my_value" == {{ my_jinja2_var }}
        "my_jinja2_var" == "foo"

    Will return "foo" for "my_value"

    """
    # keep track of parsed values
    # we want to avoid:
    # bastion_host == {{ foo }}
    # foo == {{ bastion_host }}
    if check_list is None:
        check_list = []

    if (
        isinstance(my_value, str)
        and my_value.startswith("{{")
        and my_value.endswith("}}")
    ):
        # ex: {{ my_jinja2_var }} -> lookup for 'my_jinja2_var' in hostvars
        key_name = my_value.replace("{{", "").replace("}}", "").strip()

        if key_name not in check_list:
            check_list.append(key_name)
            # resolve intricated vars
            return get_var_within(
                hostvar.get(key_name, ""), hostvar, check_list=check_list
            )
        else:
            return ""

    return my_value


def get_inv_from_command(command):
    p = subprocess.Popen(
        command,
        shell=True,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    output, error = p.communicate()
    if isinstance(output, bytes):
        output = output.decode()
    if not p.returncode:
        inventory = json.loads(output)
        return inventory
    else:
        logging.error(error)
        raise Exception("failed to get inventory")


def awx_get_inventory_file():
    # awx execution environment run dir, where the project and inventory are copied
    default_run_dir = "/runner"
    run_dir = os.environ.get("AWX_RUN_DIR", default_run_dir)
    return "{}/inventory/hosts".format(run_dir)


def awx_get_vars(host_ip, inventory_file):
    # the inventory file is a script that print the inventory in json format
    inv = get_inv_from_command(inventory_file)

    # the ssh command sent only the IP to the ansible bastion wrapper.
    # We are looking for the host which "ansible_host" has the same ip, then try to fetch the required vars from
    # its host_vars
    host = None
    for k, v in inv.get("_meta", {}).get("hostvars", {}).items():
        if v.get("ansible_host") == host_ip:
            host = k
            host_vars = v
            break

    # this should not happen
    if not host:
        return {}

    bastion_vars = get_bastion_vars(host_vars)

    if None not in [
        bastion_vars.get("bastion_host"),
        bastion_vars.get("bastion_port"),
        bastion_vars.get("bastion_user"),
    ]:
        return bastion_vars

    # if some bastion vars are missing, maybe they are defined as group_vars.
    # We do an inventory lookup to get them.
    # With AWX no need to list the whole inventory, we already know the host
    command = "ansible-inventory -i {} --host {}".format(inventory_file, host)
    return get_inv_from_command(command)


def get_bastion_vars(host_vars):
    bastion_host = host_vars.get("bastion_host")
    bastion_user = host_vars.get("bastion_user")
    bastion_port = host_vars.get("bastion_port")
    return {
        "bastion_host": bastion_host,
        "bastion_port": bastion_port,
        "bastion_user": bastion_user,
    }


def run_ssh_command(args):
    """Run an SSH command as a subprocess with Bastion error interception.

    Instead of replacing the process via os.execv(), this spawns ssh as a child
    process so that stderr can be captured and inspected.  When a non-zero exit
    code is detected, stderr is checked against known Bastion error patterns and
    a user-friendly diagnostic is prepended before the original error output.

    The function never returns — it always calls sys.exit() with the child
    process return code.
    """
    ssh_path = find_executable("ssh")
    if not ssh_path:
        sys.stderr.write(
            "[BASTION ERROR] Could not find the 'ssh' executable in PATH.\n"
        )
        sys.exit(1)

    sanitized_args = [str(e).strip() for e in args]
    sanitized_args[0] = ssh_path

    result = subprocess.run(
        sanitized_args,
        stdin=sys.stdin,
        stdout=sys.stdout,
        stderr=subprocess.PIPE,
    )

    stderr_output = (
        result.stderr.decode("utf-8", errors="replace") if result.stderr else ""
    )

    if result.returncode != 0 and stderr_output:
        # Exit code 255 signals an SSH transport-level failure (as opposed to a
        # remote command failure), which strongly indicates the error originates
        # from the Bastion connection itself rather than the target host.
        if result.returncode == 255:
            for pattern, message in BASTION_ERROR_PATTERNS.items():
                if pattern in stderr_output:
                    sys.stderr.write(message)
                    break

    # Always pass through original stderr (warnings, banners, etc.)
    if stderr_output:
        sys.stderr.write(stderr_output)
        if not stderr_output.endswith("\n"):
            sys.stderr.write("\n")

    sys.exit(result.returncode)
