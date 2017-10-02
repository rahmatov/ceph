from __future__ import print_function
import argparse
import json
import logging
from textwrap import dedent
from ceph_volume import decorators
from ceph_volume.util import disk
from . import api

logger = logging.getLogger(__name__)


osd_list_header_template = """\n
{osd_id:=^20}"""


osd_device_header_template = """

  [{type: >4}]    {path}
"""

device_metadata_item_template = """
      {tag_name: <25} {value}"""


def readable_tag(tag):
    actual_name = tag.split('.')[-1]
    return actual_name.replace('_', ' ')


def pretty_report(report):
    output = []
    for _id, devices in report.items():
        output.append(
            osd_list_header_template.format(osd_id=" osd.%s " % _id)
        )
        for device in devices:
            output.append(
                osd_device_header_template.format(
                    type=device['type'],
                    path=device['path']
                )
            )
            for tag_name, value in device.get('tags', {}).items():
                output.append(
                    device_metadata_item_template.format(
                        tag_name=readable_tag(tag_name),
                        value=value
                    )
                )
    print(''.join(output))


class List(object):

    help = 'list logical volumes and devices associated with Ceph'

    def __init__(self, argv):
        self.argv = argv

    @decorators.needs_root
    def list(self, args):
        report = self.generate(args)
        if args.format == 'json':
            print(json.dumps(report, indent=4, sort_keys=True))
        else:
            pretty_report(report)
        if not report:
            raise SystemExit()

    def generate(self, args):
        """
        Build a dictionary using the OSD ID as the key, populating it with
        devices that belong to that ID. When a specific deviced is used, try to
        match it to an LV, falling back to cross-referencing it with ``blkid``
        """
        lvs = api.Volumes()
        report = {}
        if args.device:
            lv = api.get_lv_from_argument(args.device)
            if lv:
                try:
                    _id = lv.tags['ceph.osd_id']
                except KeyError:
                    logger.warning('device is not part of ceph: %s', args.device)
                    return report

                report.setdefault(_id, [])
                report[_id].append(
                    lv.as_dict()
                )

            else:
                # this has to be a journal device (not a logical volume) so try
                # to find the PARTUUID that should be stored in the OSD logical
                # volume
                associated_lv = lvs.get(lv_tags={'ceph.journal_device': args.device})
                if associated_lv:
                    _id = associated_lv.tags['ceph.osd_id']
                    uuid = associated_lv.tags['ceph.journal_uuid']

                    report.setdefault(_id, [])
                    report[_id].append(
                        {
                            'tags': {'PARTUUID': uuid},
                            'type': 'journal',
                            'path': args.device,
                        }
                    )
            return report

        else:
            for lv in lvs:
                try:
                    _id = lv.tags['ceph.osd_id']
                except KeyError:
                    # only consider ceph-based logical volumes, everything else
                    # will get ignored
                    continue

                report.setdefault(_id, [])
                report[_id].append(
                    lv.as_dict()
                )

                journal_uuid = lv.tags['ceph.journal_uuid']
                if not api.get_lv(lv_uuid=journal_uuid):
                    # means we have a regular device, so query blkid
                    journal_device = disk.get_device_from_partuuid(journal_uuid)
                    if journal_device:
                        report[_id].append(
                            {
                                'tags': {'PARTUUID': journal_uuid},
                                'type': 'journal',
                                'path': journal_device,
                            }
                        )

            return report

    def main(self):
        sub_command_help = dedent("""
        List devices or logical volumes associated with Ceph. An association is
        determined if a device has information relating to an OSD. This is
        verified by querying LVM's metadata and correlating it with devices.

        The lvs associated with the OSD need to have been prepared previously,
        so that all needed tags and metadata exist.

        Full listing of all system devices associated with a cluster::

            ceph-volume lvm list

        List a particular device, reporting all metadata about it::

            ceph-volume lvm list /dev/sda1

        List a logical volume, along with all its metadata (vg is a volume
        group, and lv the logical volume name)::

            ceph-volume lvm list {vg/lv}
        """)
        parser = argparse.ArgumentParser(
            prog='ceph-volume lvm list',
            formatter_class=argparse.RawDescriptionHelpFormatter,
            description=sub_command_help,
        )

        parser.add_argument(
            'device',
            metavar='DEVICE',
            nargs='?',
            help='Path to an lv (as vg/lv) or to a device like /dev/sda1'
        )

        parser.add_argument(
            '--format',
            help='output format, defaults to "pretty"',
            default='pretty',
            choices=['json', 'pretty'],
        )

        args = parser.parse_args(self.argv)
        self.list(args)
