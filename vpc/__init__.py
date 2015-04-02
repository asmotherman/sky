import sys
import time
import re
import ipaddress
import logging
from operator import itemgetter
import boto
import ec2
import core

logger = logging.getLogger(__name__)

def connect_vpc():
    logger.debug('Connecting to the Amazon Virtual Private Cloud (Amazon VPC) service.')
    vpc = boto.connect_vpc(aws_access_key_id=core.args.key_id,
                           aws_secret_access_key=core.args.key)
    logger.debug('Connected to Amazon VPC.')

    return vpc

def validate_cidr_block(cidr_block):
    try:
        logger.debug('Validating CIDR block (%s).' % cidr_block)

        # Split CIDR block into Network IP and Netmask components.
        network_ip, netmask = itemgetter(0, 1)(cidr_block.split('/'))
        netmask = int(netmask)

        # Ensure that CIDR block conforms to RFC 1918.
        assert (re.search(r'^10\.([0-9]|[1-9][0-9]|[1-2][0-5][0-5])\.([0-9]|[1-9][0-9]|[1-2][0-5][0-5])\.([0-9]|[1-9][0-9]|[1-2][0-5][0-5])$', network_ip) or \
                re.search(r'^172\.(1[6-9]|2[0-9]|3[0-1])\.([0-9]|[1-9][0-9]|[1-2][0-5][0-5])\.([0-9]|[1-9][0-9]|[1-2][0-5][0-5])$', network_ip) or \
                re.search(r'^192\.168\.([0-9]|[1-9][0-9]|[1-2][0-5][0-5])\.([0-9]|[1-9][0-9]|[1-2][0-5][0-5])$', network_ip)) and \
               (re.search(r'(^[1-2]?[0-9]$)|(^3[0-2]$)', str(netmask)))

        # Validate netmask.
        if re.search(r'^10\.([0-9]|[1-9][0-9]|[1-2][0-5][0-5])\.([0-9]|[1-9][0-9]|[1-2][0-5][0-5])\.([0-9]|[1-9][0-9]|[1-2][0-5][0-5])$', network_ip):
            assert netmask >= 8
            logger.debug('Valid Class A Private Network CIDR block given (%s).' % cidr_block)
        elif re.search(r'^172\.(1[6-9]|2[0-9]|3[0-1|)\.([0-9]|[1-9][0-9]|[1-2][0-5][0-5])\.([0-9]|[1-9][0-9]|[1-2][0-5][0-5])$', network_ip):
            assert netmask >= 12
            logger.debug('Valid Class B Private Network CIDR block given (%s).' % cidr_block)
        elif re.search(r'^192\.168\.([0-9]|[1-9][0-9]|[1-2][0-5][0-5])\.([0-9]|[1-9][0-9]|[1-2][0-5][0-5])$', network_ip):
            assert netmask >= 16
            logger.debug('Valid Class C Private Network CIDR block given (%s).' % cidr_block)

        # Ensure that netmask is compatible with Amazon VPC.
        if not (netmask >= 16 and netmask <= 28):
            logger.error('Amazon VPC service requires CIDR block sizes between a /16 netmask and /28 netmask.')
            assert False

        logger.debug('CIDR block validated (%s).' % cidr_block)
        return True
    except AssertionError as error:
        logger.error('Invalid CIDR block given (%s).' % cidr_block)
        return False

def create_vpc(cidr_block, internet_connected=False):
    # Connect to the Amazon Virtual Private Cloud (Amazon VPC) service.
    vpc_connection = connect_vpc()

    # Connect to the Amazon Elastic Compute Cloud (Amazon EC2) service.
    ec2_connection = ec2.connect_ec2()

    # Generate Virtual Private Cloud (VPC) name.
    vpc_name = '-'.join(['vpc', core.PROJECT_NAME.lower(), core.args.environment.lower()])

    # Create Virtual Private Cloud (VPC).
    new_vpc = None
    try:
        logger.info('Creating Virtual Private Cloud (VPC) (%s) with CIDR block (%s).' % (vpc_name, cidr_block))
        new_vpc = vpc_connection.create_vpc(cidr_block,                 # cidr_block
                                            instance_tenancy='default',
                                            dry_run=False)
        logger.info('Created Virtual Private Cloud (VPC) (%s).' % vpc_name)
    except boto.exception.EC2ResponseError as error:
        if error.status == 400: # Bad Request
            logger.error('Error %s: %s. Could not create VPC (%s). %s' % (error.status, error.reason, vpc_name, error.message))

    # Tag Virtual Private Cloud (VPC).
    tagged = False
    while not tagged:
        try:
            tagged = ec2_connection.create_tags([new_vpc.id], {'Name': vpc_name,
                                                               'Project': core.PROJECT_NAME.lower(),
                                                               'Environment': core.args.environment.lower()})
        except boto.exception.EC2ResponseError as error:
            if error.code == 'InvalidVpcID.NotFound': # VPC hasn't registered with Virtual Private Cloud (VPC) service yet.
                pass
            else:
                raise boto.exception.EC2ResponseError

    # Tag Main Route Table.
    route_tables = vpc_connection.get_all_route_tables(filters={'vpc-id': new_vpc.id,})
    for route_table in route_tables:
        tagged = False
        route_table_name = '-'.join(['rtb', core.PROJECT_NAME.lower(), core.args.environment.lower(), 'main'])
        while not tagged:
            try:
                tagged = ec2_connection.create_tags([route_table.id], {'Name': route_table_name,
                                                                       'Project': core.PROJECT_NAME.lower(),
                                                                       'Environment': core.args.environment.lower(),
                                                                       'Type': 'main',})
            except boto.exception.EC2ResponseError as error:
                if error.code == 'InvalidID': # Route Table hasn't registered with Virtual Private Cloud (VPC) service yet.
                    pass
                else:
                    raise boto.exception.EC2ResponseError

    # Tag Access Control Lists (ACLs).
    acls = vpc_connection.get_all_network_acls(filters={'vpc-id': new_vpc.id,})
    for acl in acls:
        tagged = False
        acl_name = '-'.join(['acl', core.PROJECT_NAME.lower(), core.args.environment.lower()])
        while not tagged:
            try:
                tagged = ec2_connection.create_tags([acl.id], {'Name': acl_name,
                                                               'Project': core.PROJECT_NAME.lower(),
                                                               'Environment': core.args.environment.lower(),})
            except boto.exception.EC2ResponseError as error:
                if error.code == 'InvalidNetworkAclID.NotFound': # ACL hasn't registered with Virtual Private Cloud (VPC) service yet.
                    pass
                else:
                    raise boto.exception.EC2ResponseError

    # Tag DHCP Options Set.
    dhcp_options = vpc_connection.get_all_dhcp_options(new_vpc.dhcp_options_id)
    for dhcp_option in dhcp_options:
        tagged = False
        dhcp_option_name = '-'.join(['dopt', core.PROJECT_NAME.lower(), core.args.environment.lower()])
        while not tagged:
            try:
                tagged = ec2_connection.create_tags([dhcp_option.id], {'Name': dhcp_option_name,
                                                                       'Project': core.PROJECT_NAME.lower(),
                                                                       'Environment': core.args.environment.lower(),})
            except boto.exception.EC2ResponseError as error:
                if error.code == 'InvalidID': # DHCP Options Set hasn't registered with Virtual Private Cloud (VPC) service yet.
                    pass
                else:
                    raise boto.exception.EC2ResponseError

    if internet_connected:
        attach_internet_gateway(new_vpc)

    return new_vpc

def attach_internet_gateway(vpc):
    # Connect to the Amazon Virtual Private Cloud (Amazon VPC) service.
    vpc_connection = connect_vpc()

    # Connect to the Amazon Elastic Compute Cloud (Amazon EC2) service.
    ec2_connection = ec2.connect_ec2()

    # Create Internet Gateway.
    internet_gateway = vpc_connection.create_internet_gateway(dry_run=False)

    # Tag Internet Gateway.
    tagged = False
    internet_gateway_name = '-'.join(['igw', core.PROJECT_NAME.lower(), core.args.environment.lower()])
    while not tagged:
        try:
            tagged = ec2_connection.create_tags([internet_gateway.id], {'Name': internet_gateway_name,
                                                                        'Project': core.PROJECT_NAME.lower(),
                                                                        'Environment': core.args.environment.lower(),})
        except boto.exception.EC2ResponseError as error:
            if error.code == 'InvalidInternetGatewayID.NotFound': # IGW hasn't registered with Virtual Private Cloud (VPC) service yet.
                pass
            else:
                raise boto.exception.EC2ResponseError

    # Get name of VPC.
    vpc_tags = ec2_connection.get_all_tags(filters={'resource-id': vpc.id,
                                                    'resource-type': 'vpc'})
    vpc_name = [tag.value for tag in vpc_tags if tag.name == 'Name'][0]

    # Attach Internet Gateway to Virtual Private Cloud (VPC).
    logger.info('Attaching Internet Gateway (%s) to VPC (%s).' % (internet_gateway_name, vpc_name))
    attached = vpc_connection.attach_internet_gateway(internet_gateway.id, # internet_gateway_id
                                                      vpc.id,              # vpc_id
                                                      dry_run=False)
    if attached:
        logger.info('Attached Internet Gateway (%s).' % internet_gateway_name)
    else:
        logger.error('Could not attach Internet Gateway (%s).' % internet_gateway_name)

    return internet_gateway

def create_route_table(vpc, name=None, internet_access=False):
    # Connect to the Amazon Virtual Private Cloud (Amazon VPC) service.
    vpc_connection = connect_vpc()

    # Connect to the Amazon Elastic Compute Cloud (Amazon EC2) service.
    ec2_connection = ec2.connect_ec2()

    # Create Route Table.
    route_table = vpc_connection.create_route_table(vpc.id)

    if internet_access:
        # Get an Internet Gateway associated to the VPC.
        internet_gateways = vpc_connection.get_all_internet_gateways(filters={'attachment.vpc-id': vpc.id,
                                                                              'attachment.state': 'available',})
        if internet_gateways:
            internet_gateway = [internet_gateway for internet_gateway in internet_gateways][0] if len(internet_gateways) else None
        else:
            internet_gateway = attach_internet_gateway(vpc)

        # Add route to Internet to Route Table.
        vpc_connection.create_route(route_table.id,    # route_table_id
                                    '0.0.0.0/0',       # destination_cidr_block
                                    gateway_id=internet_gateway.id,
                                    instance_id=None,
                                    interface_id=None,
                                    vpc_peering_connection_id=None,
                                    dry_run=False)

        # Refresh Route Table.
        route_table = vpc_connection.get_all_route_tables(route_table.id)[0]

    # Generate Route Table name.
    route_tables = vpc_connection.get_all_route_tables(filters={'vpc-id': vpc.id,})
    public_route_tables = vpc_connection.get_all_route_tables(filters={'vpc-id': vpc.id,
                                                                       'route.destination-cidr-block': '0.0.0.0/0'})
    suffix = str(len(public_route_tables) if internet_access else len(route_tables)-len(public_route_tables)-1)

    if not name:
        name = '-'.join(['rtb', \
                         core.PROJECT_NAME.lower(), \
                         core.args.environment.lower(), \
                         'public' if internet_access else 'private', \
                         suffix])
    route_table.name = name

    # Tag Route Table.
    tagged = False
    while not tagged:
        try:
            tagged = ec2_connection.create_tags([route_table.id], {'Name': route_table.name,
                                                                   'Project': core.PROJECT_NAME.lower(),
                                                                   'Environment': core.args.environment.lower(),
                                                                   'Type': 'public' if internet_access else 'private',})
        except boto.exception.EC2ResponseError as error:
            if error.code == 'InvalidID': # Route Table hasn't registered with Virtual Private Cloud (VPC) service yet.
                pass
            else:
                raise boto.exception.EC2ResponseError

    logger.info('Created Route Table (%s).' % route_table.name)
    return route_table

def create_subnets(vpc, zones='All', count=1, byte_aligned=False, balanced=False, public=False):
    # Connect to the Amazon Virtual Private Cloud (Amazon VPC) service.
    vpc_connection = connect_vpc()

    # Connect to the Amazon Elastic Compute Cloud (Amazon EC2) service.
    ec2_connection = ec2.connect_ec2()

    # Break CIDR block into IP and Netmask components.
    network_ip, netmask = get_cidr_block_components(vpc.cidr_block)

    # Get/Validate Availability Zones associated with the current region.
    if isinstance(zones, str) and zones.lower() == 'all':
        zones = None
    elif isinstance(zones, str):
        zones = [zone.strip() for zone in zones.lower().split(',')]
    zones = ec2_connection.get_all_zones(zones)

    # Get the number of Subnets in each zone, so that a Subnet name can be computed.
    for zone in zones:
        offset = len(vpc_connection.get_all_subnets(filters={'vpc-id': vpc.id,
                                                             'availability-zone': zone.name,
                                                             'tag:Type': 'public' if public else 'private',}))
        zone.offset = offset

    # Get the number of Subnets within the specified VPC.
    num_subnets = len(vpc_connection.get_all_subnets(filters={'vpc-id': vpc.id,}))

    # Calculate Subnet netmask.
    subnet_netmask = netmask+len(bin(num_subnets+len(zones)*count))-3

    if balanced:
        # Balance between network expandability and network size.
        subnet_netmask = subnet_netmask+(28-subnet_netmask)//2 if subnet_netmask < 28 else subnet_netmask

    if byte_aligned:
        # Align CIDR block to nearest byte, if possible.
        subnet_netmask = subnet_netmask+8-(subnet_netmask%8) if subnet_netmask < 24 else subnet_netmask

    # Create Route Table for Public/Private Subnets.
    route_table = create_route_table(vpc, internet_access=True) if public else create_route_table(vpc, internet_access=False)

    # Create Subnets.
    subnets = list()
    for i, zone in enumerate(sorted(zones*count, key=lambda zone: zone.name)):
        # Generate Subnet name.
        suffix = '-' + str(1+zone.offset+(i%count)).zfill(len(str(zone.offset+count)))
        subnet_name = '-'.join(['subnet', \
                                core.PROJECT_NAME.lower(), \
                                core.args.environment.lower(), \
                                zone.name, \
                                ('public' if public else 'private') + suffix])

        # Shorten Subnet name, if needed and if possible.
        shortened = dict(dict.fromkeys(['resource_type', 'environment', 'subnet_type'], False))
        while len(subnet_name) > 37:
            if not shortened['resource_type']:
                subnet_name = subnet_name.replace('subnet-', '')
                shortened['resource_type'] = True
                continue
            if not shortened['subnet_type']:
                subnet_name = subnet_name.replace('public', 'pub') if public else subnet_name.replace('private', 'priv')
                shortened['subnet_type'] = True
                continue
            if not shortened['environment']:
                if core.args.environment.lower() == 'prod':
                    subnet_name = subnet_name.replace('prod', 'prd')
                elif core.args.environment.lower() == 'staging':
                    subnet_name = subnet_name.replace('staging', 'stg')
                shortened['environment'] = True
                continue
            break # Give up.

        # Create Subnet CIDR block.
        subnet_network_ip = (network_ip | (num_subnets+i << 32-subnet_netmask))
        subnet_cidr_block = str(subnet_network_ip >> 24) + '.' + \
                            str((subnet_network_ip >> 16) & 255) + '.' + \
                            str((subnet_network_ip >> 8) & 255) + '.' + \
                            str(subnet_network_ip & 255) + '/' + \
                            str(subnet_netmask)

        # Create Subnet.
        subnet = create_subnet(vpc, zone, subnet_cidr_block, subnet_name, route_table)

        # Add Subnet to list.
        if subnet:
            subnets.append(subnet)

    return subnets

def create_subnet(vpc, zone, cidr_block, subnet_name=None, route_table=None):
    # Connect to the Amazon Virtual Private Cloud (Amazon VPC) service.
    vpc_connection = connect_vpc()

    # Connect to the Amazon Elastic Compute Cloud (Amazon EC2) service.
    ec2_connection = ec2.connect_ec2()

    # Break CIDR block into IP and Netmask components.
    network_ip, netmask = get_cidr_block_components(cidr_block)

    # Create Subnet.
    try:
        logger.info('Creating Subnet (%s).' % subnet_name)
        subnet = vpc_connection.create_subnet(vpc.id,                      # vpc_id
                                              cidr_block,                  # cidr_block
                                              availability_zone=zone.name,
                                              dry_run=False)
        logger.info('Created Subnet (%s) with %s available IP addresses.' % (subnet_name, '{:,}'.format(get_network_capacity(netmask))))
    except boto.exception.BotoServerError as error:
        if error.status == 400: # Bad Request
            logger.error('Error %s: %s. Couldn\'t create Subnet (%s).' % (error.status, error.reason, subnet_name))
            if error.code == 'SubnetLimitExceeded':
                num_subnets = len(vpc_connection.get_all_subnets(filters={'vpc-id':vpc.id}))
                logging.error('%d Subnets exist within the VPC' % num_subnets)
                logging.error('Refer to the VPC User Guide for Amazon VPC Limits.')
                sys.exit(1)

    # Associate Subnet to Route Table.
    public = False
    if route_table:
        association_id = vpc_connection.associate_route_table(route_table.id, # route_table_id
                                                              subnet.id,      # subnet_id
                                                              dry_run=False)
        if len(association_id):
            logger.debug('Subnet (%s) associated to (%s).' % (subnet_name, route_table.id))
        else:
            logger.error('Subnet (%s) not associated to (%s).' % (subnet_name, route_table.id))

        # Determine Subnet type.
        public = [route for route in route_table.routes if route.gateway_id and route.destination_cidr_block == '0.0.0.0/0']

    # Tag Subnet.
    tagged = False
    while not tagged:
        try:
            tagged = ec2_connection.create_tags([subnet.id], {'Name': subnet_name,
                                                              'Project': core.PROJECT_NAME.lower(),
                                                              'Environment': core.args.environment.lower(),
                                                              'Type': 'public' if public else 'private',})
        except boto.exception.EC2ResponseError as error:
            if error.code == 'InvalidSubnetID.NotFound': # Subnet hasn't registered with Virtual Private Cloud (VPC) service yet.
                pass
            else:
                raise boto.exception.EC2ResponseError

    return subnet

def get_network_capacity(netmask):
    # Calculate the number of available IP addresses on a given network.
    available_ips = (0xffffffff ^ (0xffffffff << 32-int(netmask) & 0xffffffff))-4 # 4 addresses are reserved by Amazon
                                                                                  # for IP networking purposes.
                                                                                  # .0 for the network, .1 for the gateway,
                                                                                  # .3 for DHCP services, and .255 for broadcast.
    return available_ips

def get_default_vpc():
    # Connect to the Amazon Virtual Private Cloud (Amazon VPC) service.
    vpc_connection = connect_vpc()

    # Get all Virtual Private Clouds (VPCs).
    vpcs = vpc_connection.get_all_vpcs()

    # Return default VPC.
    default_vpc = [vpc for vpc in vpcs if vpc.is_default]
    return default_vpc[0] if len(default_vpc) else None

def get_cidr_block_components(cidr_block):
    # Break CIDR block into IP and Netmask components.
    network_ip, netmask = itemgetter(0, 1)(cidr_block.split('/'))
    network_ip = int(ipaddress.IPv4Address(network_ip))
    netmask = int(netmask)

    return (network_ip, netmask)
