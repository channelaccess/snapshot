import argparse
import re
import signal
import sys

import epics.ca
import epics.utils3

signal.signal(signal.SIGINT, signal.SIG_DFL)


def _set_default_subparser(default, subparsers=None):
    subparsers = subparsers or []

    # check if any subparser or global help was specified
    if any(arg in sys.argv[1:] for arg in subparsers + ['-h', '--help']):
        return
    else:
        sys.argv.insert(1, default)


def _support_old_args(args_replacements):
    for idx, arg in enumerate(sys.argv[1:]):
        arg_replacement = args_replacements.get(arg, None)
        if arg_replacement is not None:
            sys.argv[idx + 1] = arg_replacement


def save(args):
    from .cmd import save
    save(
        args.FILE,
        args.out,
        args.macro,
        args.force,
        args.timeout,
        args.labels,
        args.comment)


def restore(args):
    from .cmd import restore
    restore(args.FILE, args.force, args.timeout)


def gui(args):
    from .gui import start_gui
    start_gui(req_file_path=args.FILE, req_file_macros=args.macro,
              save_dir=args.dir, force=args.force, default_labels=args.labels,
              force_default_labels=args.force_labels, init_path=args.base,
              config_path=args.config, trace_execution=args.trace_execution)


def main():
    """ Main creates Qt application and handles arguments """

    # A workaround for inconsistent string encodings, needed because PSI does
    # not enforce a particular encoding. With this setting, pyepics will
    # backslash-escape all non-latin1 characters read from CA and reencode them
    # back to whatever they were when writing to CA. This also allows
    # transparent (de)serialization of arbitrary encodings. Display is broken,
    # of course, because you can't display a string with undefined encoding.
    epics.utils3.EPICS_STR_ENCODING = 'raw_unicode_escape'

    args_pars = argparse.ArgumentParser(
        formatter_class=argparse.RawDescriptionHelpFormatter)
    args_pars.set_defaults(macro=None)
    subparsers = args_pars.add_subparsers(
        help='modes of work (if not specified \"gui\" will be used)')
    args_pars.format_usage()

    # Gui
    gui_pars = subparsers.add_parser(
        'gui', help='open graphical interface (default)')
    gui_pars.set_defaults(func=gui)
    gui_pars.add_argument('FILE', nargs='?', help='request file.')
    gui_pars.add_argument(
        '-m',
        '--macro',
        help="macros for request file e.g.: \"SYS=TEST,DEV=D1\"")
    gui_pars.add_argument('-d', '--dir',
                          help="directory for saved snapshot files")
    gui_pars.add_argument('-b', '--base',
                          help="base directory for request files")
    gui_pars.add_argument('-f', '--force',
                          help="force save/restore in case of disconnected PVs", action='store_true')
    gui_pars.add_argument('--labels', type=str,
                          help="list of comma separated predefined labels e.g.: \"label_1,label_2\"")
    gui_pars.add_argument(
        '--force_labels',
        help="force predefined labels",
        action='store_true')
    gui_pars.add_argument('--config', help="path to configuration file")
    gui_pars.add_argument(
        '--trace-execution',
        help="print info during long-running tasks",
        action='store_true')

    # Save
    save_pars = subparsers.add_parser(
        'save', help='save current state of PVs to file without using GUI')
    save_pars.set_defaults(func=save)
    save_pars.add_argument('FILE', help='request file.')
    save_pars.add_argument('-m', '--macro',
                           help="macros for request file e.g.: \"SYS=TEST,DEV=D1\"")
    save_pars.add_argument(
        '-o',
        '--out',
        default='.',
        help="Output path/file.")
    save_pars.add_argument('-f', '--force',
                           help="force save in case of disconnected PVs after timeout", action='store_true')
    save_pars.add_argument('--labels', default='',
                           help="list of comma separated labels e.g.: \"label_1,label_2\"")
    save_pars.add_argument('--comment', default='', help="Comment")
    save_pars.add_argument(
        '--timeout',
        default=10,
        type=int,
        help='max time waiting for PVs to be connected')

    # Restore
    rest_pars = subparsers.add_parser(
        'restore', help='restore saved state of PVs from file without using GUI')
    rest_pars.set_defaults(func=restore)
    rest_pars.add_argument('FILE', help='saved snapshot file')
    rest_pars.add_argument('-f', '--force',
                           help="force restore in case of disconnected PVs after timeout", action='store_true')
    rest_pars.add_argument('--timeout', default=10, type=int,
                           help='max time waiting for PVs to be connected and restored')

    # Following two functions modify sys.argv
    _set_default_subparser('gui', ['gui', 'save', 'restore'])
    # From version 1.3.1 handling of options have changed to be more consistent. However following function replaces
    # old style options with new style equivalents (backward compatibility).Old style options are no more shown in the
    # help, so users are encouraged to use new style.
    _support_old_args({'-macro': '--macro',
                       '-dir': '--dir',
                       '-out': '--out',
                       '-base': '--base',
                       '-timeout': '--timeout'})

    # Prepare epilog text for main help
    args_pars.epilog = '''------- GUI mode --------
usage: {}       {}
-------- Command line save mode --------
{}
-------- Command line restore mode --------
{}'''.format(
        re.sub('(?:\sgui|usage:\s)', '', gui_pars.format_usage()),
        re.sub('usage:\s', '', gui_pars.format_help()),
        save_pars.format_help(),
        rest_pars.format_help()
    )

    args_pars.description = '''Tool for saving and restoring snapshots of EPICS process variables (PVs).
Can be used as graphical interface tool or a command line tool.'''

    args = args_pars.parse_args()

    args.func(args)


# Start the application here
if __name__ == '__main__':
    main()
