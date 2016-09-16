from base import MgmtSystemAPIBase
from collections import namedtuple
from rest_client import ContainerClient
from urllib import quote as urlquote
from enum import Enum
from websocket_client import HawkularWebsocketClient

import re
import sys

"""
Related yaml structures:

[cfme_data]
management_systems:
    hawkular:
        name: My hawkular
        type: hawkular
        hostname: 10.12.13.14
        port: 8080
        credentials: hawkular
        authenticate: true
        rest_protocol: http

[credentials]
hawkular:
    username: admin
    password: secret
"""

Feed = namedtuple('Feed', ['id', 'path'])
ResourceType = namedtuple('ResourceType', ['id', 'name', 'path'])
Resource = namedtuple('Resource', ['id', 'name', 'path'])
ResourceData = namedtuple('ResourceData', ['name', 'path', 'value'])
Server = namedtuple('Server', ['id', 'name', 'path', 'data'])
ServerGroup = namedtuple('ServerGroup', ['id', 'name', 'path', 'data'])
Domain = namedtuple('Domain', ['id', 'name', 'path', 'data'])
Messaging = namedtuple('Messaging', ['id', 'name', 'path'])
Deployment = namedtuple('Deployment', ['id', 'name', 'path'])
Datasource = namedtuple('Datasource', ['id', 'name', 'path'])
OperationType = namedtuple('OperationType', ['id', 'name', 'path'])
ServerStatus = namedtuple('ServerStatus', ['address', 'version', 'state', 'product', 'host'])
Event = namedtuple('event', ['id', 'eventType', 'ctime', 'dataSource', 'dataId',
                             'category', 'text', 'tags', 'tenantId', 'context'])

CANONICAL_PATH_NAME_MAPPING = {
    '/d;': 'data_id',
    '/e;': 'environment_id',
    '/f;': 'feed_id',
    '/m;': 'metric_id',
    '/mp;': 'metadata_pack_id',
    '/mt;': 'metric_type_id',
    '/ot;': 'operation_type_id',
    '/r;': 'resource_id',
    '/rl;': 'relationship_id',
    '/rt;': 'resource_type_id',
    '/t;': 'tenant_id',
}


class CanonicalPath(object):
    """CanonicalPath class

    Path is class to split canonical path to friendly values.\
    If the path has more than one entry for a resource result will be in list
    Example:
        obj_p = Path('/t;28026b36-8fe4-4332-84c8-524e173a68bf\
        /f;88db6b41-09fd-4993-8507-4a98f25c3a6b\
        /r;Local~~/r;Local~%2Fdeployment%3Dhawkular-command-gateway-war.war')
        obj_p.path returns raw path
        obj_p.tenant returns tenant as `28026b36-8fe4-4332-84c8-524e173a68bf`
        obj_p.feed returns feed as `88db6b41-09fd-4993-8507-4a98f25c3a6b`
        obj_p.resource returns as \
        `[u'Local~~', u'Local~%2Fdeployment%3Dhawkular-command-gateway-war.war']`

    Args:
        path:   The canonical path. Example: /t;28026b36-8fe4-4332-84c8-524e173a68bf\
        /f;88db6b41-09fd-4993-8507-4a98f25c3a6b/r;Local~~

    """

    def __init__(self, path):
        if path is None or len(path) == 0:
            raise KeyError("CanonicalPath should not be None or empty!")
        self._path_ids = []
        r_paths = re.split(r'(/\w+;)', path)
        if len(r_paths) % 2 == 1:
            del r_paths[0]
        for p_index in range(0, len(r_paths), 2):
            path_id = CANONICAL_PATH_NAME_MAPPING[r_paths[p_index]]
            path_value = r_paths[p_index + 1]
            if path_id in self._path_ids:
                if isinstance(getattr(self, path_id), list):
                    ex_list = getattr(self, path_id)
                    ex_list.append(path_value)
                    setattr(self, path_id, ex_list)
                else:
                    v_list = [
                        getattr(self, path_id),
                        path_value
                    ]
                    setattr(self, path_id, v_list)
            else:
                self._path_ids.append(path_id)
                setattr(self, path_id, path_value)

    def __iter__(self):
        """This enables you to iterate through like it was a dictionary, just without .iteritems"""
        for path_id in self._path_ids:
            yield (path_id, getattr(self, path_id))

    def __repr__(self):
        return "<CanonicalPath {}>".format(self.to_string)

    @property
    def to_string(self):
        c_path = ''
        if 'tenant_id' in self._path_ids:
            c_path = "/t;{}".format(self.tenant_id)
        if 'feed_id' in self._path_ids:
            c_path += "/f;{}".format(self.feed_id)
        if 'environment_id' in self._path_ids:
            c_path += "/e;{}".format(self.environment_id)
        if 'metric_id' in self._path_ids:
            c_path += "/m;{}".format(self.metric_id)
        if 'resource_id' in self._path_ids:
            if isinstance(self.resource_id, list):
                for _resource_id in self.resource_id:
                    c_path += "/r;{}".format(_resource_id)
            else:
                c_path += "/r;{}".format(self.resource_id)
        if 'metric_type_id' in self._path_ids:
            c_path += "/mt;{}".format(self.metric_type_id)
        if 'resource_type_id' in self._path_ids:
            c_path += "/rt;{}".format(self.resource_type_id)
        if 'metadata_pack_id' in self._path_ids:
            c_path += "/mp;{}".format(self.metadata_pack_id)
        if 'operation_type_id' in self._path_ids:
            c_path += "/ot;{}".format(self.operation_type_id)
        if 'relationship_id' in self._path_ids:
            c_path += "/rl;{}".format(self.relationship_id)
        return c_path


class Hawkular(MgmtSystemAPIBase):
    """Hawkular management system

    Hawkular REST API method calls.
    Will be used by cfme_tests project to verify Hawkular content shown in CFME UI

    Args:
        hostname: The Hawkular hostname.
        protocol: Hawkular REST API protocol. Default value: 'http'
        port: Hawkular REST API port on provided host. Default value: '8080'.
        entry: Hawkular REST API entry point URI. Default value: 'hawkular/inventory'
        username: The username to connect with.
        password: The password to connect with.

    """

    def __init__(self,
                 hostname, protocol="http", port=8080, **kwargs):
        super(Hawkular, self).__init__(kwargs)
        self.hostname = hostname
        self.port = port
        self.username = kwargs.get('username', 'jdoe')
        self.password = kwargs.get('password', 'password')
        self.tenant_id = kwargs.get('tenant_id', 'hawkular')
        self.auth = self.username, self.password
        self._hawkular = HawkularService(hostname=hostname, port=port, auth=self.auth,
                                         protocol=protocol, tenant_id=self.tenant_id,
                                         entry="hawkular")
        self._alert = HawkularAlert(hostname=hostname, port=port, auth=self.auth,
                                    protocol=protocol, tenant_id=self.tenant_id)
        self._inventory = HawkularInventory(hostname=hostname, port=port, auth=self.auth,
                                            protocol=protocol, tenant_id=self.tenant_id)
        self._metric = HawkularMetric(hostname=hostname, port=port, auth=self.auth,
                                      protocol=protocol, tenant_id=self.tenant_id)
        self._operation = HawkularOperation(hostname=self.hostname, port=self.port,
                                            username=self.username, password=self.password,
                                            tenant_id=self.tenant_id,
                                            connect=kwargs.get('ws_connect', True))

    _stats_available = {
        'num_server': lambda self: len(self.inventory.list_server()),
        'num_domain': lambda self: len(self.inventory.list_domain()),
        'num_deployment': lambda self: len(self.inventory.list_server_deployment()),
        'num_datasource': lambda self: len(self.inventory.list_server_datasource()),
        'num_messaging': lambda self: len(self.inventory.list_messaging()),
    }

    @property
    def alert(self):
        return self._alert

    @property
    def inventory(self):
        return self._inventory

    @property
    def metric(self):
        return self._metric

    @property
    def operation(self):
        return self._operation

    def _check_inv_version(self, version):
        return version in self._get_inv_json('status')['Implementation-Version']

    def info(self):
        raise NotImplementedError('info not implemented.')

    def clone_vm(self, source_name, vm_name):
        raise NotImplementedError('clone_vm not implemented.')

    def create_vm(self, vm_name):
        raise NotImplementedError('create_vm not implemented.')

    def current_ip_address(self, vm_name):
        raise NotImplementedError('current_ip_address not implemented.')

    def delete_vm(self, vm_name):
        raise NotImplementedError('delete_vm not implemented.')

    def deploy_template(self, template, *args, **kwargs):
        raise NotImplementedError('deploy_template not implemented.')

    def disconnect(self):
        self.operation.close()

    def does_vm_exist(self, name):
        raise NotImplementedError('does_vm_exist not implemented.')

    def get_ip_address(self, vm_name):
        raise NotImplementedError('get_ip_address not implemented.')

    def is_vm_running(self, vm_name):
        raise NotImplementedError('is_vm_running not implemented.')

    def is_vm_stopped(self, vm_name):
        raise NotImplementedError('is_vm_stopped not implemented.')

    def is_vm_suspended(self, vm_name):
        raise NotImplementedError('is_vm_suspended not implemented.')

    def list_flavor(self):
        raise NotImplementedError('list_flavor not implemented.')

    def list_template(self):
        raise NotImplementedError('list_template not implemented.')

    def list_vm(self, **kwargs):
        raise NotImplementedError('list_vm not implemented.')

    def remove_host_from_cluster(self, hostname):
        raise NotImplementedError('remove_host_from_cluster not implemented.')

    def restart_vm(self, vm_name):
        raise NotImplementedError('restart_vm not implemented.')

    def start_vm(self, vm_name):
        raise NotImplementedError('start_vm not implemented.')

    def stop_vm(self, vm_name):
        raise NotImplementedError('stop_vm not implemented.')

    def suspend_vm(self, vm_name):
        raise NotImplementedError('restart_vm not implemented.')

    def vm_status(self, vm_name):
        raise NotImplementedError('vm_status not implemented.')

    def wait_vm_running(self, vm_name, num_sec):
        raise NotImplementedError('wait_vm_running not implemented.')

    def wait_vm_stopped(self, vm_name, num_sec):
        raise NotImplementedError('wait_vm_stopped not implemented.')

    def wait_vm_suspended(self, vm_name, num_sec):
        raise NotImplementedError('wait_vm_suspended not implemented.')

    def status(self):
        """Returns status of hawkular services"""
        return {
            'hawkular_services': self._hawkular.status(),
            'alerts': self.alert.status(),
            'inventory': self.inventory.status(),
            'metrics': self.metric.status()
        }


class HawkularService(object):
    def __init__(self, hostname, port, protocol, auth, tenant_id, entry):
        """This class is parent class for all hawkular services
        Args:
            hostname: hostname of the hawkular server
            port: port number of hawkular server
            protocol: protocol for the hawkular server
            auth: Either a (user, pass) sequence or a string with token
            tenant_id: tenant id for the current session
            entry: entry point of a service url
        """
        self.auth = auth
        self.hostname = hostname
        self.port = port
        self.protocol = protocol
        self.tenant_id = tenant_id
        self._api = ContainerClient(hostname=hostname, auth=self.auth, protocol=protocol,
                                    port=port, entry=entry)

    def status(self):
        """Returns status of a service"""
        return self._get(path='status')

    def _get(self, path, params=None):
        """runs GET request and returns response as JSON"""
        return self._api.get_json(path, headers={"Hawkular-Tenant": self.tenant_id}, params=params)

    def _delete(self, path):
        """runs DELETE request and returns status"""
        return self._api.delete_status(path, headers={"Hawkular-Tenant": self.tenant_id})

    def _put(self, path, data):
        """runs PUT request and returns status"""
        return self._api.put_status(path, data, headers={"Hawkular-Tenant": self.tenant_id,
                                                         "Content-Type": "application/json"})

    def _post(self, path, data):
        """runs POST request and returns status"""
        return self._api.post_status(path, data,
                                     headers={"Hawkular-Tenant": self.tenant_id,
                                              "Content-Type": "application/json"})


class HawkularAlert(HawkularService):
    def __init__(self, hostname, port, protocol, auth, tenant_id):
        """Creates hawkular alert service instance. For args refer 'HawkularService'"""
        HawkularService.__init__(self, hostname=hostname, port=port, protocol=protocol,
                                 auth=auth, tenant_id=tenant_id, entry="hawkular/alerts")

    def list_event(self, start_time=0, end_time=sys.maxsize):
        """Returns the list of events.
        Filtered by provided start time and end time. Or lists all events if no argument provided.
        This information is wrapped into Event.

         Args:
             start_time: Start time as timestamp
             end_time: End time as timestamp
         """
        entities = []
        entities_j = self._get('events?startTime={}&endTime={}'.format(start_time, end_time))
        if entities_j:
            for entity_j in entities_j:
                entity = Event(entity_j['id'], entity_j['eventType'], entity_j['ctime'],
                               entity_j['dataSource'], entity_j.get('dataId', None),
                               entity_j['category'], entity_j['text'], entity_j.get('tags', None),
                               entity_j.get('tenantId', None), entity_j.get('context', None))
                entities.append(entity)
        return entities


class HawkularInventory(HawkularService):
    def __init__(self, hostname, port, protocol, auth, tenant_id):
        """Creates hawkular inventory service instance. For args refer 'HawkularService'"""
        HawkularService.__init__(self, hostname=hostname, port=port, protocol=protocol,
                                 auth=auth, tenant_id=tenant_id, entry="hawkular/inventory")

    _stats_available = {
        'num_server': lambda self: len(self.list_server()),
        'num_domain': lambda self: len(self.list_domain()),
        'num_deployment': lambda self: len(self.list_server_deployment()),
        'num_datasource': lambda self: len(self.list_server_datasource()),
        'num_messaging': lambda self: len(self.list_messaging()),
    }

    def list_server_deployment(self, feed_id=None):
        """Returns list of server deployments.

        Args:
            feed_id: Feed id of the resource (optional)
        """
        resources = self.list_resource(feed_id=feed_id, resource_type_id='Deployment')
        deployments = []
        if resources:
            for resource in resources:
                deployments.append(Deployment(resource.id, resource.name, resource.path))
        return deployments

    def list_messaging(self, feed_id=None):
        """Returns list of massagings (JMS Queue and JMS Topic).

          Args:
            feed_id: Feed id of the resource (optional)
        """
        resources = self.list_resource(feed_id=feed_id, resource_type_id='JMS Queue')
        resources.extend(self.list_resource(
            feed_id=feed_id,
            resource_type_id='JMS Topic'))
        messagings = []
        if resources:
            for resource in resources:
                messagings.append(Messaging(resource.id, resource.name, resource.path))
        return messagings

    def list_server(self, feed_id=None):
        """Returns list of middleware servers.

          Args:
            feed_id: Feed id of the resource (optional)
        """
        resources = self.list_resource(feed_id=feed_id, resource_type_id='WildFly Server')
        resources.extend(self.list_resource(
            feed_id=feed_id,
            resource_type_id='Domain WildFly Server'))
        servers = []
        if resources:
            for resource in resources:
                resource_data = self.get_config_data(
                    feed_id=resource.path.feed_id,
                    resource_id=self._get_resource_id(resource.path.resource_id))
                server_data = resource_data.value
                servers.append(Server(resource.id, resource.name, resource.path, server_data))
        return servers

    def list_domain(self, feed_id=None):
        """Returns list of middleware domains.

          Args:
            feed_id: Feed id of the resource (optional)
        """
        resources = self.list_resource(feed_id=feed_id, resource_type_id='Host Controller')
        domains = []
        if resources:
            for resource in resources:
                resource_data = self.get_config_data(
                    feed_id=resource.path.feed_id, resource_id=resource.id)
                domain_data = resource_data.value
                domains.append(Domain(resource.id, resource.name, resource.path, domain_data))
        return domains

    def list_server_group(self, feed_id):
        """Returns list of middleware domain's server groups.

          Args:
            feed_id: Feed id of the resource (optional)
        """
        resources = self.list_resource(feed_id=feed_id, resource_type_id='Domain Server Group')
        server_groups = []
        if resources:
            for resource in resources:
                resource_data = self.get_config_data(
                    feed_id=resource.path.feed_id,
                    resource_id=self._get_resource_id(resource.path.resource_id))
                server_group_data = resource_data.value
                server_groups.append(ServerGroup(
                    resource.id, resource.name, resource.path, server_group_data))
        return server_groups

    def list_resource(self, resource_type_id, feed_id=None):
        """Returns list of resources.

          Args:
            feed_id: Feed id of the resource (optional)
            resource_type_id: Resource type id
        """
        if not feed_id:
            resources = []
            for feed in self.list_feed():
                resources.extend(self._list_resource(feed_id=feed.path.feed_id,
                                                     resource_type_id=resource_type_id))
            return resources
        else:
            return self._list_resource(feed_id=feed_id, resource_type_id=resource_type_id)

    def list_child_resource(self, feed_id, resource_id, recursive=False):
        """Returns list of resources.

          Args:
            feed_id: Feed id of the resource
            resource_id: Resource id
            recursive: should be True when you want to get recursively, Default False
        """
        if not feed_id or not resource_id:
            raise KeyError("'feed_id' and 'resource_id' are a mandatory field!")
        resources = []
        if recursive:
            entities_j = self._get('traversal/f;{}/r;{}/recursive;over=isParentOf;type=r'
                                   .format(feed_id, resource_id))
        else:
            entities_j = self._get('traversal/f;{}/r;{}/type=r'
                                   .format(feed_id, resource_id))
        if entities_j:
            for entity_j in entities_j:
                resources.append(Resource(entity_j['id'], entity_j['name'],
                                          CanonicalPath(entity_j['path'])))
        return resources

    def _list_resource(self, feed_id, resource_type_id=None):
        """Returns list of resources.

         Args:
            feed_id: Feed id of the resource
            resource_type_id: Resource type id (optional)
        """
        if not feed_id:
            raise KeyError("'feed_id' is a mandatory field!")
        entities = []
        if resource_type_id:
            entities_j = self._get('traversal/f;{}/rt;{}/rl;defines/type=r'
                                   .format(feed_id, resource_type_id))
        else:
            entities_j = self._get('traversal/f;{}/type=r'.format(feed_id))
        if entities_j:
            for entity_j in entities_j:
                entities.append(Resource(entity_j['id'], entity_j['name'],
                                         CanonicalPath(entity_j['path'])))
        return entities

    def get_config_data(self, feed_id, resource_id):
        """Returns the data/configuration information about resource by provided

        Args:
            feed_id: Feed id of the resource
            resource_id: Resource id
         """
        if not feed_id or not resource_id:
            raise KeyError("'feed_id' and 'resource_id' are mandatory field!")
        entity_j = self._get('entity/f;{}/r;{}/d;configuration'
                             .format(feed_id, self._get_resource_id(resource_id)))
        if entity_j:
            return ResourceData(entity_j['name'], CanonicalPath(entity_j['path']),
                                entity_j['value'])
        return None

    def _get_resource_id(self, resource_id):
        if isinstance(resource_id, list):
            return "{}".format('/r;'.join(resource_id))
        else:
            return resource_id

    def list_feed(self):
        """Returns list of feeds"""
        entities = []
        entities_j = self._get('traversal/type=f')
        if entities_j:
            for entity_j in entities_j:
                entities.append(Feed(entity_j['id'], CanonicalPath(entity_j['path'])))
        return entities

    def list_resource_type(self, feed_id):
        """Returns list of resource types.

         Args:
            feed_id: Feed id of the resource type
        """
        if not feed_id:
            raise KeyError("'feed_id' is a mandatory field!")
        entities = []
        entities_j = self._get('traversal/f;{}/type=rt'.format(feed_id))
        if entities_j:
            for entity_j in entities_j:
                entities.append(ResourceType(entity_j['id'], entity_j['name'], entity_j['path']))
        return entities

    def list_operation_definition(self, feed_id, resource_type_id):
        """Lists operations definitions

        Args:
            feed_id: Feed id of the operation
            resource_type_id: Resource type id of the operation
        """
        if feed_id is None or resource_type_id is None:
            raise KeyError("'feed_id' and 'resource_type_id' are mandatory fields!")
        res_j = self._get('traversal/f;{}/rt;{}/type=ot'.format(feed_id, resource_type_id))
        operations = []
        if res_j:
            for res in res_j:
                operations.append(OperationType(res['id'], res['name'], CanonicalPath(res['path'])))
        return operations

    def list_server_datasource(self, feed_id=None):
        """Returns list of datasources.

         Args:
             feed_id: Feed id of the datasource (optional)
        """
        resources = self.list_resource(feed_id=feed_id, resource_type_id='Datasource')
        datasources = []
        if resources:
            for resource in resources:
                datasources.append(Datasource(resource.id, resource.name, resource.path))
        return datasources

    def edit_config_data(self, resource_data, **kwargs):
        """Edits the data.value information for resource by provided

        Args:
            resource_data: Resource data
        """
        if not isinstance(resource_data, ResourceData) or not resource_data.value:
            raise KeyError(
                "'resource_data' should be ResourceData with 'value' attribute")
        if not kwargs or 'feed_id' not in kwargs or 'resource_id' not in kwargs:
            raise KeyError("'feed_id' and 'resource_id' are mandatory field!")
        r = self._put('entity/f;{}/r;{}/d;configuration'
                      .format(kwargs['feed_id'], kwargs['resource_id']),
                      {"value": resource_data.value})
        return r

    def create_resource(self, resource, resource_data, resource_type, **kwargs):
        """Creates new resource and creates it's data by provided
        Args:
            resource: resource
            kwargs: feed_id, resource_id and required fields
            resource_data: Resource data
            resource_type: Resource type
        """
        if not isinstance(resource, Resource):
            raise KeyError("'resource' should be an instance of Resource")
        if not isinstance(resource_data, ResourceData) or not resource_data.value:
            raise KeyError(
                "'resource_data' should be ResourceData with 'value' attribute")
        if not isinstance(resource_type, ResourceType):
            raise KeyError("'resource_type' should be an instance of ResourceType")
        if not kwargs or 'feed_id' not in kwargs:
            raise KeyError('Variable "feed_id" id mandatory field!')

        resource_id = urlquote(resource.id, safe='')
        r = self._post('entity/f;{}/resource'.format(kwargs['feed_id']),
                       data={"name": resource.name, "id": resource.id,
                             "resourceTypePath": "rt;{}"
                       .format(resource_type.path.resource_type_id)})
        if r:
            r = self._post('entity/f;{}/r;{}/data'
                           .format(kwargs['feed_id'], resource_id),
                           data={'role': 'configuration', "value": resource_data.value})
        else:
            # if resource or it's data was not created correctly, delete resource
            self._delete('entity/f;{}/r;{}'.format(kwargs['feed_id'], resource_id))
        return r

    def delete_resource(self, feed_id, resource_id):
        """Removed a resource.
        Args:
            feed_id: Feed id of the data source
            resource_id: Resource id of the datasource
        """
        if not feed_id or not resource_id:
            raise KeyError("'feed_id' and 'resource_id' are mandatory fields!")
        r = self._delete('entity/f;{}/r;{}'.format(feed_id, resource_id))
        return r


class HawkularMetric(HawkularService):
    def __init__(self, hostname, port, protocol, auth, tenant_id):
        """Creates hawkular metric service instance. For args refer 'HawkularService'"""
        HawkularService.__init__(self, hostname=hostname, port=port, protocol=protocol,
                                 auth=auth, tenant_id=tenant_id, entry="hawkular/metrics")

    @staticmethod
    def _metric_id_availability_feed(feed_id):
        return "hawkular-feed-availability-{}".format(feed_id)

    @staticmethod
    def _metric_id_availability_server(feed_id, server_id):
        return "AI~R~[{}/{}~~]~AT~Server Availability~Server Availability"\
            .format(feed_id, server_id)

    @staticmethod
    def _metric_id_availability_deployment(feed_id, server_id, resource_id):
        return "AI~R~[{}/{}~/deployment={}]~AT~Deployment Status~Deployment Status"\
            .format(feed_id, server_id, resource_id)

    @staticmethod
    def _metric_id_guage_server(feed_id, server_id, metric_enum):
        if not isinstance(metric_enum, MetricEnumGauge):
            raise KeyError("'metric_enum' should be a type of 'MetricEnumGauge' Enum class")
        return "MI~R~[{}/{}~~]~MT~{}~{}".format(feed_id, server_id, metric_enum.metric_type,
                                                metric_enum.sub_type)

    @staticmethod
    def _metric_id_guage_datasource(feed_id, server_id, resource_id, metric_enum):
        if not isinstance(metric_enum, MetricEnumGauge):
            raise KeyError("'metric_enum' should be a type of 'MetricEnumGauge' Enum class")
        return "MI~R~[{}/{}~/subsystem=datasources/data-source={}]~MT~{}~{}" \
            .format(feed_id, server_id, resource_id, metric_enum.metric_type, metric_enum.sub_type)

    @staticmethod
    def _metric_id_counter_server(feed_id, server_id, metric_enum):
        if not isinstance(metric_enum, MetricEnumCounter):
            raise KeyError("'metric_enum' should be a type of 'MetricEnumCounter' Enum class")
        if MetricEnumCounter.SVR_TXN_NUMBER_OF_TRANSACTIONS.metric_type == metric_enum.metric_type:
            metric_id = "MI~R~[{}/{}~/subsystem=transactions]~MT~{}~{}" \
                .format(feed_id, server_id, metric_enum.metric_type, metric_enum.sub_type)
        else:
            metric_id = "MI~R~[{}/{}~~]~MT~{}~{}".format(feed_id, server_id,
                                                         metric_enum.metric_type,
                                                         metric_enum.sub_type)
        return metric_id

    @staticmethod
    def _metric_id_counter_deployment(feed_id, server_id, resource_id, metric_enum):
        if not isinstance(metric_enum, MetricEnumCounter):
            raise KeyError("'metric_enum' should be a type of 'MetricEnumCounter' Enum class")
        return "MI~R~[{}/{}~/deployment={}]~MT~{}~{}".format(feed_id, server_id, resource_id,
                                                             metric_enum.metric_type,
                                                             metric_enum.sub_type)

    def list_availability_feed(self, feed_id, **kwargs):
        """Returns list of DataPoint of a feed
        Args:
            feed_id: Feed id of the metric resource
            kwargs: Refer ``list_availability``
        """
        metric_id = self._metric_id_availability_feed(feed_id=feed_id)
        return self.list_availability(metric_id=metric_id, **kwargs)

    def list_availability_server(self, feed_id, server_id, **kwargs):
        """Returns list of `DataPoint` of a server
        Args:
            feed_id: Feed id of the server
            server_id: Server id
            kwargs: Refer ``list_availability``
        """
        metric_id = self._metric_id_availability_server(feed_id=feed_id, server_id=server_id)
        return self.list_availability(metric_id=metric_id, **kwargs)

    def list_availability_deployment(self, feed_id, server_id, resource_id, **kwargs):
        """Returns list of `DataPoint` of a deployment
        Args:
            feed_id: Feed id of the deployment
            server_id: Server id of the deployment
            resource_id: deployment id
            kwargs: Refer ``list_availability``
        """
        metric_id = self._metric_id_availability_deployment(feed_id=feed_id, server_id=server_id,
                                                            resource_id=resource_id)
        return self.list_availability(metric_id=metric_id, **kwargs)

    def list_availability(self, metric_id, **kwargs):
        """Returns list of `DataPoint` of a metric
        Args:
            metric_id: Metric id
            kwargs: refer optional query params and query type

        Optional query params:
            start: timestamp, Defaults to now: 8 hours
            end: timestamp, Defaults to now
            buckets: Total number of buckets
            bucketDuration: Bucket duration
            distinct: Set to true to return only distinct, contiguous values
            limit: Limit the number of data points returned
            order: Data point sort order, based on timestamp [values: ASC, DESC]

        Query type:
            raw: set True when you want to get raw data, Default False which returns stats
        """
        prefix_id = "availability/{}".format(urlquote(metric_id, safe=''))
        return self._list_data(prefix_id=prefix_id, **kwargs)

    def list_gauge_datasource(self, feed_id, server_id, resource_id, metric_enum, **kwargs):
        """Returns list of NumericBucketPoint of datasource metric
            Args:
                feed_id: feed id of the datasource
                server_id: server id of the datasource
                resource_id: resource id, here which is datasource id
                metric_enum: Any one of *DS_* Enum value from ``MetricEnumGauge``
                kwargs: Refer ``list_gauge``
            """
        metric_id = self._metric_id_guage_datasource(feed_id=feed_id, server_id=server_id,
                                                     resource_id=resource_id,
                                                     metric_enum=metric_enum)
        return self.list_gauge(metric_id=metric_id, **kwargs)

    def list_gauge_server(self, feed_id, server_id, metric_enum, **kwargs):
        """Returns list of `NumericBucketPoint` of server metric
            Args:
                feed_id: feed id of the server
                server_id: server id
                metric_enum: Any one of *SVR_* ``Enum`` value from ``MetricEnumGauge``
                kwargs: Refer ``list_gauge``
            """
        metric_id = self._metric_id_guage_server(feed_id=feed_id, server_id=server_id,
                                                 metric_enum=metric_enum)
        return self.list_gauge(metric_id=metric_id, **kwargs)

    def list_gauge(self, metric_id, **kwargs):
        """Returns list of `NumericBucketPoint` of a metric
            Args:
                metric_id: Metric id
                kwargs: Refer optional query params and query type

            Optional query params:
                start: timestamp, Defaults to now: 8 hours
                end: timestamp, Defaults to now
                buckets: Total number of buckets
                bucketDuration: Bucket duration
                distinct: Set to true to return only distinct, contiguous values
                limit: Limit the number of data points returned
                order: Data point sort order, based on timestamp [values: ASC, DESC]

            Query type:
                raw: set True when you want to get raw data, Default False which returns stats
                rate: set True when you want rate data default False
                stats: return stats data default True
            """
        prefix_id = "gauges/{}".format(urlquote(metric_id, safe=''))
        return self._list_data(prefix_id=prefix_id, **kwargs)

    def list_counter_server(self, feed_id, server_id, metric_enum, **kwargs):
        """Returns list of `NumericBucketPoint` of server metric
            Args:
                feed_id: feed id of the server
                server_id: server id
                metric_enum: Any one of *SVR_* ``Enum`` value from ``MetricEnumCounter``
                kwargs: Refer ``list_counter``
            """
        metric_id = self._metric_id_counter_server(feed_id=feed_id, server_id=server_id,
                                                   metric_enum=metric_enum)
        return self.list_counter(metric_id=metric_id, **kwargs)

    def list_counter_deployment(self,
                                feed_id, server_id, resource_id, metric_enum, **kwargs):
        """Returns list of `NumericBucketPoint` of server metric
            Args:
                feed_id: feed id of the deployment
                server_id: server id of the deployment
                resource_id: resource id, that's deployment id
                metric_enum: Any one of *DEP_* ``Enum`` value from ``MetricEnumCounter``
                kwargs: Refer ``list_counter``
            """
        metric_id = self._metric_id_counter_deployment(feed_id=feed_id, server_id=server_id,
                                                       resource_id=resource_id,
                                                       metric_enum=metric_enum)
        return self.list_counter(metric_id=metric_id, **kwargs)

    def list_counter(self, metric_id, **kwargs):
        """Returns list of `NumericBucketPoint` of a metric
            Args:
                metric_id: metric id
                kwargs: Refer optional query params and query type

            Optional query params:
                start: timestamp, Defaults to now: 8 hours
                end: timestamp, Defaults to now
                buckets: Total number of buckets
                bucketDuration: Bucket duration
                distinct: Set to true to return only distinct, contiguous values
                limit: Limit the number of data points returned
                order: Data point sort order, based on timestamp [values: ASC, DESC]

            Query type:
                raw: set True when you want to get raw data, Default False which returns stats
                rate: set True when you want rate data default False
                stats: return stats data default True
            """
        prefix_id = "counters/{}".format(urlquote(metric_id, safe=''))
        return self._list_data(prefix_id=prefix_id, **kwargs)

    def list_availability_definition(self):
        """Lists all availability type metric definitions"""
        return self._get(path='availability')

    def list_gauge_definition(self):
        """Lists all gauge type metric definitions"""
        return self._get(path='gauges')

    def list_counter_definition(self):
        """Lists all counter type metric definitions"""
        return self._get(path='counters')

    def list_definition(self):
        """Lists all metric definitions"""
        return self._get(path='metrics')

    def _list_data(self, prefix_id, **kwargs):
        params = {
            'start': kwargs.get('start', None),
            'end': kwargs.get('end', None),
            'bucketDuration': kwargs.get('bucket_duration', None),
            'buckets': kwargs.get('buckets', None),
            'percentiles': kwargs.get('percentiles', None),
            'limit': kwargs.get('limit', None),
            'order': kwargs.get('order', None),
        }
        if kwargs.get('bucketDuration', None) is not None:
            params['bucketDuration'] = kwargs.get('bucketDuration')
        raw = kwargs.get('raw', False)
        rate = kwargs.get('rate', False)
        if not raw and params['bucketDuration'] is None and params['buckets'] is None:
            raise KeyError("Either the 'buckets' or 'bucket_duration' parameter must be used")
        if rate:
            return self._get(path='{}/rate/stats'.format(prefix_id), params=params)
        elif raw:
            return self._get(path='{}/raw'.format(prefix_id), params=params)
        else:
            return self._get(path='{}/stats'.format(prefix_id), params=params)

    def add_availability_feed(self, data, feed_id):
        """Add availability data for a feed
        Args:
            data: list of DataPoint
            feed_id: feed id
        """
        metric_id = self._metric_id_availability_feed(feed_id=feed_id)
        self.add_availability(data=data, metric_id=metric_id)

    def add_availability_server(self, data, feed_id, server_id):
        """Add availability data for a server
        Args:
            data: list of DataPoint
            feed_id: feed id
            server_id: servier id
        """
        metric_id = self._metric_id_availability_server(feed_id=feed_id, server_id=server_id)
        self.add_availability(data=data, metric_id=metric_id)

    def add_availability_deployment(self, data, feed_id, server_id, resource_id):
        """Add availability data for a deployment
        Args:
            data: list of DataPoint
            feed_id: feed id
            server_id: server id
            resource_id: resource id (deployment id)
        """
        metric_id = self._metric_id_counter_deployment(feed_id=feed_id, server_id=server_id,
                                                       resource_id=resource_id)
        self.add_availability(data=data, metric_id=metric_id)

    def add_gauge_server(self, data, feed_id, server_id, metric_enum):
        """Add guage data for a server
        Args:
            data: list of DataPoint
            feed_id: feed id
            server_id: server id
            metric_enum: type of MetricEmumGuage
        """
        metric_id = self._metric_id_gauge_server(feed_id=feed_id, server_id=server_id,
                                                 metric_enum=metric_enum)
        self.add_gauge(data=data, metric_id=metric_id)

    def add_gauge_datasource(self, data, feed_id, server_id, resource_id, metric_enum):
        """Add guage data for a datasource
        Args:
            data: list of DataPoint
            feed_id: feed id
            server_id: server id
            resource_id: resource id (datasource id)
            metric_enum: type of MetricEmumGuage
        """
        metric_id = self._metric_id_guage_datasource(feed_id=feed_id, server_id=server_id,
                                                     resource_id=resource_id,
                                                     metric_enum=metric_enum)
        self.add_gauge(data=data, metric_id=metric_id)

    def add_counter_server(self, data, feed_id, server_id, metric_enum):
        """Add counter data for a server
        Args:
            data: list of DataPoint
            feed_id: feed id
            server_id: server id
            metric_enum: type of MetricEmumCounter
        """
        metric_id = self._metric_id_counter_server(feed_id=feed_id, server_id=server_id,
                                                   metric_enum=metric_enum)
        self.add_counter(data=data, metric_id=metric_id)

    def add_counter_deployment(self, data, feed_id, server_id, resource_id, metric_enum):
        """Add counter data for a deployment
            Args:
                data: list of DataPoint
                feed_id: feed id
                server_id: server id
                resource_id: resource id (deployment id)
                metric_enum: type of MetricEmumCounter
            """
        metric_id = self._metric_id_counter_deployment(feed_id=feed_id, server_id=server_id,
                                                       resource_id=resource_id,
                                                       metric_enum=metric_enum)
        self.add_counter(data=data, metric_id=metric_id)

    def add_string(self, data, metric_id=None):
        """Add string data for a metric or metrics
            Args:
                data: list of DataPoint
                metric_id: metric id
            """
        self._post_data(prefix_id='strings', data=data, metric_id=metric_id)

    def add_gauge(self, data, metric_id=None):
        """Add guage data for a metric or metrics
            Args:
                data: list of DataPoint
                metric_id: metric id
            """
        self._post_data(prefix_id='gauges', data=data, metric_id=metric_id)

    def add_counter(self, data, metric_id=None):
        """Add counter data for a metric or metrics
            Args:
                data: list of DataPoint
                metric_id: metric id
            """
        self._post_data(prefix_id='counters', data=data, metric_id=metric_id)

    def add_availability(self, data, metric_id=None):
        """Add availability data for a metric or metrics
            Args:
                data: list of DataPoint
                metric_id: metric id
            """
        self._post_data(prefix_id='availability', data=data, metric_id=metric_id)

    def _post_data(self, prefix_id, data, metric_id=None):
        if metric_id:
            metric_id = urlquote(metric_id, safe='')
            self._post(path='{}/{}/raw'.format(prefix_id, metric_id), data=data)
        else:
            self._post(path='{}/raw'.format(prefix_id), data=data)


class HawkularOperation(object):
    def __init__(self, hostname, port, username, password, tenant_id, connect=True):
        """Creates hawkular command gateway websocket client service instance.
        Args:
            hostname: hostname or IP of the server
            port: port number of the server
            username: username of the server
            password: password of the server
            tenant_id: tenant id of the server
            connect: If you do not want to connect on initialization pass this as False
        """
        self.cmd_gw_ws_api = HawkularWebsocketClient(
            url="ws://{}:{}/hawkular/command-gateway/ui/ws".format(hostname, port),
            headers={"Hawkular-Tenant": tenant_id, "Accept": "application/json"},
            username=username, password=password)
        self.tenant_id = tenant_id
        if connect:
            self.cmd_gw_ws_api.connect()

    def add_jdbc_driver(self, feed_id, server_id, driver_name, module_name,
                        driver_class, driver_jar_name=None, binary_content=None,
                        binary_file_location=None):
        """Adds JDBC driver on specified server under specified feed. return status
        Args:
            feed_id: feed id of the server
            server_id: server id under a feed
            driver_name: driver name
            module_name: module name
            driver_class: driver class
            driver_jar_name: driver jar file name
            binary_content: driver file content in binary format
            binary_file_location: driver file location(on local disk)
        """
        if driver_jar_name and not binary_content and not binary_file_location:
            raise KeyError("If 'driver_jar_name' field is set the jar file must be passed"
                           " as binary or file location")
        resource_path = "/t;{}/f;{}/r;{}~~".format(self.tenant_id, feed_id, server_id)
        payload = {"resourcePath": resource_path, "driverJarName": driver_jar_name,
                   "driverName": driver_name, "moduleName": module_name,
                   "driverClass": driver_class}
        return self.cmd_gw_ws_api.hwk_invoke_operation(operation_name="AddJdbcDriver",
                                                       payload=payload,
                                                       binary_file_location=binary_file_location,
                                                       binary_content=binary_content)

    def remove_jdbc_driver(self, feed_id, server_id, driver_name):
        """Removes JDBC driver on specified server under specified feed. return status
        Args:
            feed_id: feed id of the server
            server_id: server id under a feed
            driver_name: driver name
        """
        payload = {"resourcePath": "/t;{}/f;{}/r;{}~%2Fsubsystem%3Ddatasources%2Fjdbc-driver%3D{}"
            .format(self.tenant_id, feed_id, server_id, driver_name)}
        return self.cmd_gw_ws_api.hwk_invoke_operation(operation_name="RemoveJdbcDriver",
                                                       payload=payload)

    def add_deployment(self, feed_id, server_id, destination_file_name, force_deploy=False,
                       enabled=True, server_groups=None, binary_file_location=None,
                       binary_content=None):
        """Adds deployment to hawkular server. Return status
        Args:
            feed_id: feed id of the server
            server_id: server id under a feed
            destination_file_name: resulting file name
            force_deploy: whether to replace existing content or not (default = false)
            enabled: whether the deployment should be enabled immediately, or not (default = true)
            server_groups: comma-separated list of server groups for the operation (default = None)
            binary_content: driver file content in binary format
            binary_file_location: driver file location(on local disk)
        """
        if not binary_content and not binary_file_location:
            raise KeyError("Deployment file must be passed as binary or file location")
        resource_path = "/t;{}/f;{}/r;{}~~".format(self.tenant_id, feed_id, server_id)
        payload = {"destinationFileName": destination_file_name, "forceDeploy": force_deploy,
                   "resourcePath": resource_path, "enabled": enabled, "serverGroups": server_groups}
        return self.cmd_gw_ws_api.hwk_invoke_operation(operation_name="DeployApplication",
                                                       payload=payload,
                                                       binary_content=binary_content,
                                                       binary_file_location=binary_file_location)

    def undeploy(self, feed_id, server_id, destination_file_name, remove_content=True,
                 server_groups=None):
        """Removes deployment on a hawkular server. Return status
        Args:
            feed_id: feed id of the server
            server_id: server id under a feed
            destination_file_name: deployment file name
            remove_content: whether to remove the deployment content or not (default = true)
            server_groups: comma-separated list of server groups for the operation (default = None)
        """
        resource_path = "/t;{}/f;{}/r;{}~~".format(self.tenant_id, feed_id, server_id)
        payload = {"destinationFileName": destination_file_name, "removeContent": remove_content,
                   "serverGroups": server_groups, "resourcePath": resource_path}
        return self.cmd_gw_ws_api.hwk_invoke_operation(operation_name="UndeployApplication",
                                                       payload=payload)

    def enable_deployment(self, feed_id, server_id, destination_file_name, server_groups=None):
        """Enables deployment on a hawkular server. Return status
        Args:
            feed_id: feed id of the server
            server_id: server id under a feed
            destination_file_name: deployment file name
            server_groups: comma-separated list of server groups for the operation (default = None)
            """
        resource_path = "/t;{}/f;{}/r;{}~~".format(self.tenant_id, feed_id, server_id)
        payload = {"destinationFileName": destination_file_name, "serverGroups": server_groups,
                   "resourcePath": resource_path}
        return self.cmd_gw_ws_api.hwk_invoke_operation(operation_name="EnableApplication",
                                                       payload=payload)

    def disable_deployment(self, feed_id, server_id, destination_file_name, server_groups=None):
        """Disable deployment on a hawkular server. Return status
        Args:
            feed_id: feed id of the server
            server_id: server id under a feed
            destination_file_name: deployment file name
            server_groups: comma-separated list of server groups for the operation (default = None)
        """
        resource_path = "/t;{}/f;{}/r;{}~~".format(self.tenant_id, feed_id, server_id)
        payload = {"destinationFileName": destination_file_name, "serverGroups": server_groups,
                   "resourcePath": resource_path}
        return self.cmd_gw_ws_api.hwk_invoke_operation(operation_name="DisableApplication",
                                                       payload=payload)

    def restart_deployment(self, feed_id, server_id, destination_file_name, server_groups=None):
        """Restarts deployment on a hawkular server. Return status
        Args:
            feed_id: feed id of the server
            server_id: server id under a feed
            destination_file_name: deployment file name
            server_groups: comma-separated list of server groups for the operation (default = None)
            """
        resource_path = "/t;{}/f;{}/r;{}~~".format(self.tenant_id, feed_id, server_id)
        payload = {"destinationFileName": destination_file_name, "serverGroups": server_groups,
                   "resourcePath": resource_path}
        return self.cmd_gw_ws_api.hwk_invoke_operation(operation_name="RestartApplication",
                                                       payload=payload)

    def close_ws(self):
        """Closes web socket client session"""
        self.cmd_gw_ws_api.close()


class MetricEnum(Enum):
    """Enum to define Metrics type and sub type. This is base for all Enum types in metrics"""

    def __init__(self, metric_type, sub_type):
        self.metric_type = metric_type  # metric type
        self.sub_type = sub_type  # sub type


class MetricEnumGauge(MetricEnum):
    """Enum to define Gauge metric types and sub types"""
    DS_POOL_ACTIVE_COUNT = ("Datasource Pool Metrics", "Active Count")
    DS_POOL_AVAILABLE_COUNT = ("Datasource Pool Metrics", "Available Count")
    DS_POOL_AVERAGE_BLOCKING_TIME = ("Datasource Pool Metrics", "Average Blocking Time")
    DS_POOL_AVERAGE_CREATION_TIME = ("Datasource Pool Metrics", "Average Creation Time")
    DS_POOL_AVERAGE_GET_TIME = ("Datasource Pool Metrics", "Average Get Time")
    DS_POOL_BLOCKING_FAILURE_COUNT = ("Datasource Pool Metrics", "Blocking Failure Count")
    DS_POOL_CREATED_COUNT = ("Datasource Pool Metrics", "Created Count")
    DS_POOL_DESTROYED_COUNT = ("Datasource Pool Metrics", "Destroyed Count")
    DS_POOL_IDLE_COUNT = ("Datasource Pool Metrics", "Idle Count")
    DS_POOL_IN_USE_COUNT = ("Datasource Pool Metrics", "In Use Count")
    DS_POOL_MAX_CREATION_TIME = ("Datasource Pool Metrics", "Max Creation Time")
    DS_POOL_MAX_GET_TIME = ("Datasource Pool Metrics", "Max Get Time")
    DS_POOL_MAX_USED_COUNT = ("Datasource Pool Metrics", "Max Used Count")
    DS_POOL_MAX_WAIT_COUNT = ("Datasource Pool Metrics", "Max Wait Count")
    DS_POOL_MAX_WAIT_TIME = ("Datasource Pool Metrics", "Max Wait Time")
    DS_POOL_TIMED_OUT = ("Datasource Pool Metrics", "Timed Out")
    DS_POOL_TOTAL_BLOCKING_TIME = ("Datasource Pool Metrics", "Total Blocking Time")
    DS_POOL_TOTAL_CREATION_TIME = ("Datasource Pool Metrics", "Total Creation Time")
    DS_POOL_TOTAL_GET_TIME = ("Datasource Pool Metrics", "Total Get Time")
    DS_POOL_WAIT_COUNT = ("Datasource Pool Metrics", "Wait Count")
    SVR_MEM_HEAP_COMMITTED = ("WildFly Memory Metrics", "Heap Committed")
    SVR_MEM_HEAP_MAX = ("WildFly Memory Metrics", "Heap Max")
    SVR_MEM_HEAP_USED = ("WildFly Memory Metrics", "Heap Used")
    SVR_MEM_NON_HEAP_COMMITTED = ("WildFly Memory Metrics", "NonHeap Committed")
    SVR_MEM_NON_HEAP_USED = ("WildFly Memory Metrics", "NonHeap Used")
    SVR_TH_THREAD_COUNT = ("WildFly Threading Metrics", "Thread Count")
    SVR_WEB_AGGREGATED_ACTIVE_WEB_SESSIONS = \
        ("WildFly Aggregated Web Metrics", "Aggregated Active Web Sessions")
    SVR_WEB_AGGREGATED_MAX_ACTIVE_WEB_SESSIONS = \
        ("WildFly Aggregated Web Metrics", "Aggregated Max Active Web Sessions")


class MetricEnumCounter(MetricEnum):
    """Enum Counter metric types and sub types"""
    DEP_UTM_EXPIRED_SESSIONS = ("Undertow Metrics", "Expired Sessions")
    DEP_UTM_REJECTED_SESSIONS = ("Undertow Metrics", "Rejected Sessions")
    DEP_UTM_SESSIONS_CREATED = ("Undertow Metrics", "Sessions Created")
    SVR_MEM_ACCUMULATED_GC_DURATION = ("WildFly Memory Metrics", "Accumulated GC Duration")
    SVR_TXN_NUMBER_OF_ABORTED_TRANSACTIONS = \
        ("Transactions Metrics", "Number of Aborted Transactions")
    SVR_TXN_NUMBER_OF_APPLICATION_ROLLBACKS = \
        ("Transactions Metrics", "Number of Application Rollbacks")
    SVR_TXN_NUMBER_OF_COMMITTED_TRANSACTIONS = \
        ("Transactions Metrics", "Number of Committed Transactions")
    SVR_TXN_NUMBER_OF_HEURISTICS = ("Transactions Metrics", "Number of Heuristics")
    SVR_TXN_NUMBER_OF_NESTED_TRANSACTIONS = \
        ("Transactions Metrics", "Number of Nested Transactions")
    SVR_TXN_NUMBER_OF_RESOURCE_ROLLBACKS = ("Transactions Metrics", "Number of Resource Rollbacks")
    SVR_TXN_NUMBER_OF_TIMED_OUT_TRANSACTIONS = \
        ("Transactions Metrics", "Number of Timed Out Transactions")
    SVR_TXN_NUMBER_OF_TRANSACTIONS = ("Transactions Metrics", "Number of Transactions")
    SVR_WEB_AGGREGATED_EXPIRED_WEB_SESSIONS = \
        ("WildFly Aggregated Web Metrics", "Aggregated Expired Web Sessions")
    SVR_WEB_AGGREGATED_REJECTED_WEB_SESSIONS = \
        ("WildFly Aggregated Web Metrics", "Aggregated Rejected Web Sessions")
    SVR_WEB_AGGREGATED_SERVLET_REQUEST_COUNT = \
        ("WildFly Aggregated Web Metrics", "Aggregated Servlet Request Count")
    SVR_WEB_AGGREGATED_SERVLET_REQUEST_TIME = \
        ("WildFly Aggregated Web Metrics", "Aggregated Servlet Request Time")
