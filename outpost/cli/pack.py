"""outpost pack: turn box/ into signed tar.xz files, to carry or publish."""

from pathlib import Path

from outpost import box, pki
from outpost.cli.output import say
from outpost.workspace import Workspace


def add_parser(commands) -> None:
    parser = commands.add_parser("pack", help="turn box/ into signed tar.xz files")
    parser.add_argument("--out", default="dist", help="output folder (default: dist)")
    parser.set_defaults(run=run)


def run(args) -> int:
    workspace = Workspace.open(args.dir)
    _public_key, signing_key = pki.ensure_signing_key(workspace.pki_dir, workspace.secrets_dir)
    made = box.pack(workspace.box_dir, Path(args.out), signer=lambda manifest: pki.sign(manifest, signing_key))
    for path in made:
        say(f"  {path}  ({path.stat().st_size / 1e6:.0f} MB)")
    say(f"Signed with secrets/box.key; verify with pki/box.pub. Upload with: outpost upload {args.out}/*.tar.xz")
    return 0
