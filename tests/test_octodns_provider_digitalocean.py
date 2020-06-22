#
#
#


from __future__ import absolute_import, division, print_function, \
    unicode_literals

from mock import Mock, call
from os.path import dirname, join
from requests import HTTPError
from requests_mock import ANY, mock as requests_mock
from six import text_type
from unittest import TestCase

from octodns.record import Record
from octodns.provider.digitalocean import DigitalOceanClientNotFound, \
    DigitalOceanProvider
from octodns.provider.yaml import YamlProvider
from octodns.zone import Zone


class TestDigitalOceanProvider(TestCase):
    expected = Zone('unit.tests.', [])
    source = YamlProvider('test', join(dirname(__file__), 'config'))
    source.populate(expected)

    # Our test suite differs a bit, add our NS and remove the simple one
    expected.add_record(Record.new(expected, 'under', {
        'ttl': 3600,
        'type': 'NS',
        'values': [
            'ns1.unit.tests.',
            'ns2.unit.tests.',
        ]
    }))
    for record in list(expected.records):
        if record.name == 'sub' and record._type == 'NS':
            expected._remove_record(record)
            break

    def test_populate(self):
        provider = DigitalOceanProvider('test', 'token')

        # Bad auth
        with requests_mock() as mock:
            mock.get(ANY, status_code=401,
                     text='{"id":"unauthorized",'
                     '"message":"Unable to authenticate you."}')

            with self.assertRaises(Exception) as ctx:
                zone = Zone('unit.tests.', [])
                provider.populate(zone)
            self.assertEquals('Unauthorized', text_type(ctx.exception))

        # General error
        with requests_mock() as mock:
            mock.get(ANY, status_code=502, text='Things caught fire')

            with self.assertRaises(HTTPError) as ctx:
                zone = Zone('unit.tests.', [])
                provider.populate(zone)
            self.assertEquals(502, ctx.exception.response.status_code)

        # Non-existent zone doesn't populate anything
        with requests_mock() as mock:
            mock.get(ANY, status_code=404,
                     text='{"id":"not_found","message":"The resource you '
                     'were accessing could not be found."}')

            zone = Zone('unit.tests.', [])
            provider.populate(zone)
            self.assertEquals(set(), zone.records)

        # No diffs == no changes
        with requests_mock() as mock:
            base = 'https://api.digitalocean.com/v2/domains/unit.tests/' \
                'records?page='
            with open('tests/fixtures/digitalocean-page-1.json') as fh:
                mock.get('{}{}'.format(base, 1), text=fh.read())
            with open('tests/fixtures/digitalocean-page-2.json') as fh:
                mock.get('{}{}'.format(base, 2), text=fh.read())

            zone = Zone('unit.tests.', [])
            provider.populate(zone)
            self.assertEquals(12, len(zone.records))
            changes = self.expected.changes(zone, provider)
            self.assertEquals(1, len(changes))

        # 2nd populate makes no network calls/all from cache
        again = Zone('unit.tests.', [])
        provider.populate(again)
        self.assertEquals(12, len(again.records))

        # bust the cache
        del provider._zone_records[zone.name]

    def test_apply(self):
        provider = DigitalOceanProvider('test', 'token')

        resp = Mock()
        resp.json = Mock()
        provider._client._request = Mock(return_value=resp)

        domain_after_creation = {
            "domain_records": [{
                "id": 11189874,
                "type": "NS",
                "name": "@",
                "data": "ns1.digitalocean.com",
                "priority": None,
                "port": None,
                "ttl": 3600,
                "weight": None,
                "flags": None,
                "tag": None
            }, {
                "id": 11189875,
                "type": "NS",
                "name": "@",
                "data": "ns2.digitalocean.com",
                "priority": None,
                "port": None,
                "ttl": 3600,
                "weight": None,
                "flags": None,
                "tag": None
            }, {
                "id": 11189876,
                "type": "NS",
                "name": "@",
                "data": "ns3.digitalocean.com",
                "priority": None,
                "port": None,
                "ttl": 3600,
                "weight": None,
                "flags": None,
                "tag": None
            }, {
                "id": 11189877,
                "type": "A",
                "name": "@",
                "data": "192.0.2.1",
                "priority": None,
                "port": None,
                "ttl": 3600,
                "weight": None,
                "flags": None,
                "tag": None
            }],
            "links": {},
            "meta": {
                "total": 4
            }
        }

        # non-existent domain, create everything
        resp.json.side_effect = [
            DigitalOceanClientNotFound,  # no zone in populate
            DigitalOceanClientNotFound,  # no domain during apply
            domain_after_creation
        ]
        plan = provider.plan(self.expected)

        # No root NS, no ignored, no excluded, no unsupported
        n = len(self.expected.records) - 7
        self.assertEquals(n, len(plan.changes))
        self.assertEquals(n, provider.apply(plan))
        self.assertFalse(plan.exists)

        provider._client._request.assert_has_calls([
            # created the domain
            call('POST', '/domains', data={'ip_address': '192.0.2.1',
                                           'name': 'unit.tests'}),
            # get all records in newly created zone
            call('GET', '/domains/unit.tests/records', {'page': 1}),
            # delete the initial A record
            call('DELETE', '/domains/unit.tests/records/11189877'),
            # created at least some of the record with expected data
            call('POST', '/domains/unit.tests/records', data={
                'data': '1.2.3.4',
                'name': '@',
                'ttl': 300, 'type': 'A'}),
            call('POST', '/domains/unit.tests/records', data={
                'data': '1.2.3.5',
                'name': '@',
                'ttl': 300, 'type': 'A'}),
            call('POST', '/domains/unit.tests/records', data={
                'data': 'ca.unit.tests.',
                'flags': 0, 'name': '@',
                'tag': 'issue',
                'ttl': 3600, 'type': 'CAA'}),
            call('POST', '/domains/unit.tests/records', data={
                'name': '_srv._tcp',
                'weight': 20,
                'data': 'foo-1.unit.tests.',
                'priority': 10,
                'ttl': 600,
                'type': 'SRV',
                'port': 30
            }),
        ])
        self.assertEquals(24, provider._client._request.call_count)

        provider._client._request.reset_mock()

        # delete 1 and update 1
        provider._client.records = Mock(return_value=[
            {
                'id': 11189897,
                'name': 'www',
                'data': '1.2.3.4',
                'ttl': 300,
                'type': 'A',
            },
            {
                'id': 11189898,
                'name': 'www',
                'data': '2.2.3.4',
                'ttl': 300,
                'type': 'A',
            },
            {
                'id': 11189899,
                'name': 'ttl',
                'data': '3.2.3.4',
                'ttl': 600,
                'type': 'A',
            }
        ])

        # Domain exists, we don't care about return
        resp.json.side_effect = ['{}']

        wanted = Zone('unit.tests.', [])
        wanted.add_record(Record.new(wanted, 'ttl', {
            'ttl': 300,
            'type': 'A',
            'value': '3.2.3.4'
        }))

        plan = provider.plan(wanted)
        self.assertTrue(plan.exists)
        self.assertEquals(2, len(plan.changes))
        self.assertEquals(2, provider.apply(plan))
        # recreate for update, and delete for the 2 parts of the other
        provider._client._request.assert_has_calls([
            call('POST', '/domains/unit.tests/records', data={
                'data': '3.2.3.4',
                'type': 'A',
                'name': 'ttl',
                'ttl': 300
            }),
            call('DELETE', '/domains/unit.tests/records/11189899'),
            call('DELETE', '/domains/unit.tests/records/11189897'),
            call('DELETE', '/domains/unit.tests/records/11189898')
        ], any_order=True)
