"""Resolve per-role eval models and efforts without assuming a model catalogue."""

import json
from pathlib import Path

ROLES = ('implementation', 'review', 'supervisor', 'baseline')
DEFAULT_MODELS = {'implementation': 'gpt-6-luna', 'review': 'gpt-5.6-terra', 'supervisor': 'gpt-5.6-terra'}
DEFAULT_EFFORTS = {'implementation': 'low', 'review': 'medium', 'supervisor': 'medium'}


def nonempty_string(value):
    if not isinstance(value, str) or not value.strip() or '\0' in value:
        raise ValueError('must be a nonempty string without NUL characters')
    return value


def add_arguments(parser):
    parser.add_argument('--config', type=Path, help='JSON defaults and per-case model/effort overrides')
    for role in ROLES:
        for kind, defaults in (('model', DEFAULT_MODELS), ('effort', DEFAULT_EFFORTS)):
            default = defaults.get(role, 'resolved supervisor ' + kind)
            parser.add_argument('--' + role + '-' + kind, type=nonempty_string,
                                help=role + ' ' + kind + '; overrides config (default: ' + default + ')')


def object_fields(value, allowed, location):
    if not isinstance(value, dict):
        raise ValueError(location + ' must be an object')
    unknown = set(value) - set(allowed)
    if unknown:
        raise ValueError(location + ': unknown fields ' + ', '.join(sorted(unknown)))


def validate_roles(section, location):
    object_fields(section, ('models', 'efforts'), location)
    for kind, roles in section.items():
        object_fields(roles, ROLES, location + '.' + kind)
        for role, value in roles.items():
            try:
                nonempty_string(value)
            except ValueError as error:
                raise ValueError(location + '.' + kind + '.' + role + ' ' + str(error)) from None


def load_config(path, known_cases):
    config = json.loads(path.read_text()) if path is not None else {}
    object_fields(config, ('defaults', 'cases'), 'config')
    validate_roles(config.get('defaults', {}), 'config.defaults')
    cases = config.get('cases', {})
    object_fields(cases, known_cases, 'config.cases')
    for name, section in cases.items():
        validate_roles(section, 'config.cases.' + name)
    return config


def cli_overrides(args):
    return {kind + 's': {role: getattr(args, role + '_' + kind) for role in ROLES
                        if getattr(args, role + '_' + kind) is not None}
            for kind in ('model', 'effort')}


def resolve_config(config, case_id=None, overrides=None):
    resolved = {'models': dict(DEFAULT_MODELS), 'efforts': dict(DEFAULT_EFFORTS)}
    for section in (config.get('defaults', {}), config.get('cases', {}).get(case_id, {}), overrides or {}):
        for kind, roles in section.items():
            resolved[kind].update(roles)
    for roles in resolved.values():
        if 'baseline' not in roles:
            roles['baseline'] = roles['supervisor']
    return resolved
