import re
import time
import random
import logging
import boto
import iam
import ec2
import core

logger = logging.getLogger(__name__)

def connect_ec2():
    logger.debug('Connecting to the Amazon Elastic Compute Cloud (Amazon EC2) service.')
    ec2 = boto.connect_ec2(aws_access_key_id=core.args.key_id,
                           aws_secret_access_key=core.args.key)
    logger.debug('Connected to Amazon EC2.')

    return ec2

def create_security_group(vpc, name=None, allowed_inbound_traffic=None, allowed_outbound_traffic=None):
    # Connect to the Amazon Elastic Compute Cloud (Amazon EC2) service.
    ec2_connection = connect_ec2()

    # Generate Security Group name.
    if not name:
        sg_name = '-'.join(['gp', core.PROJECT_NAME.lower(), core.args.environment.lower()])
    else:
        sg_name = name

    # Create Security Group.
    logger.info('Creating Security Group (%s).' % sg_name)
    security_group = ec2_connection.create_security_group(sg_name, 'Security Group Description', vpc_id=vpc.id)
    logger.info('Created Security Group (%s).' % sg_name)

    # Set up inbound/outbound rules.
    ec2_connection.revoke_security_group_egress(security_group.id, -1, from_port=0, to_port=65535, cidr_ip='0.0.0.0/0')
    for (protocol, target, rule_type) in [(traffic[0].upper(), traffic[1], 'inbound') for traffic in allowed_inbound_traffic] + \
                                         [(traffic[0].upper(), traffic[1], 'outbound') for traffic in allowed_outbound_traffic]:

        # Determine whether target is a CIDR block or a Security Group.
        is_cidr_ip = re.search('^(25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)\.'+ # A in A.B.C.D
                               '(25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)\.' + # B in A.B.C.D
                               '(25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)\.' + # C in A.B.C.D
                               '(25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)'   + # D in A.B.C.D
                               '(/(([1-2]?[0-9])|(3[0-2])))$', target) # /0 through /32
        target_cidr_ip = target if is_cidr_ip else None
        target_group = target if not is_cidr_ip else None

        # Create inbound rules.
        if rule_type == 'inbound':
            if protocol == 'HTTP':
                security_group.authorize(ip_protocol='tcp', from_port=80, to_port=80, src_group=target_group, cidr_ip=target_cidr_ip)
            if protocol == 'HTTPS':
                security_group.authorize(ip_protocol='tcp', from_port=443, to_port=443, src_group=target_group, cidr_ip=target_cidr_ip)
            logger.info('Security Group (%s) allowed inbound %s traffic from %s.' % (sg_name, protocol, target))

        # Create outbound rules.
        if rule_type == 'outbound':
            if protocol == 'HTTP':
                ec2_connection.authorize_security_group_egress(security_group.id, 'tcp', from_port=80, to_port=80, src_group_id=target_group, cidr_ip=target_cidr_ip)
            if protocol == 'HTTPS':
                ec2_connection.authorize_security_group_egress(security_group.id, 'tcp', from_port=443, to_port=443, src_group_id=target_group, cidr_ip=target_cidr_ip)
            if protocol == 'DNS':
                ec2_connection.authorize_security_group_egress(security_group.id, 'tcp', from_port=53, to_port=53, src_group_id=target_group, cidr_ip=target_cidr_ip)
                ec2_connection.authorize_security_group_egress(security_group.id, 'udp', from_port=53, to_port=53, src_group_id=target_group, cidr_ip=target_cidr_ip)
            logger.info('Security Group (%s) allowed outbound %s traffic to %s.' % (sg_name, protocol, target))

    return security_group

def create_elb(sg, subnets, cert_arn):
    # Connect to the Amazon EC2 Load Balancing (Amazon ELB) service.
    logger.info('Connecting to the Amazon EC2 Load Balancing (Amazon ELB) service.')
    elb_connection = boto.connect_elb()
    logger.info('Connected to the Amazon EC2 Load Balancing (Amazon ELB) service.')

    # Connect to the Amazon Elastic Compute Cloud (Amazon EC2) service.
    ec2_connection = ec2.connect_ec2()

    # Generate Elastic Load Balancer (ELB) name.
    elb_name = '-'.join(['elb', core.PROJECT_NAME.lower(), core.args.environment.lower()])

    # Delete existing Elastic Load Balancer (ELB).
    logger.info('Deleting Elastic Load Balancer (%s).' % elb_name)
    try:
        elb_connection.delete_load_balancer(elb_name)
    except boto.exception.BotoServerError as error:
        if error.status == 400: # Bad Request
            logger.error('Couldn\'t delete Elastic Load Balancer (%s) due to a malformed request %s: %s.' % (elb_name, error.status, error.reason))
        if error.status == 404: # Not Found
            logger.error('Elastic Load Balancer (%s) was not found. Error %s: %s.' % (elb_name, error.status, error.reason))
    logger.info('Deleted Elastic Load Balancer (%s).' % elb_name)

    # Create Elastic Load Balancer (ELB).
    logger.info('Creating Elastic Load Balancer (%s).' % elb_name)
    load_balancer = elb_connection.create_load_balancer(elb_name, # name
                                                        None,     # zones         - Valid only for load balancers in EC2-Classic.
                                                        listeners=[(80,80,'HTTP'),
                                                                   (443,443,'HTTPS',cert_arn)],
                                                        subnets=[subnet.id for subnet in subnets],
                                                        security_groups=[sg.id],
                                                        scheme='internet-facing', # Valid only for load balancers in EC2-VPC.
                                                        complex_listeners=None)
    logger.info('Created Elastic Load Balancer (%s).' % elb_name)

    return load_balancer

def create_nat_instances(vpc, subnets, security_groups=None, script=None, instance_profile_name=None, os='ubuntu', image_id=None):
    instances = list()

    if not security_groups:
        security_groups = [create_security_group(vpc, allowed_inbound_traffic=[('HTTP',   '0.0.0.0/0')
                                                                              ,('HTTPS',  '0.0.0.0/0')]
                                                    , allowed_outbound_traffic=[('HTTP',  '0.0.0.0/0')
                                                                               ,('HTTPS', '0.0.0.0/0')
                                                                               ,('DNS',   '0.0.0.0/0')])]

    return instances

def create_ec2_instances(vpc, subnets, security_groups=None, script=None, instance_profile_name=None, os='ubuntu', image_id=None):
    # Create a security group, if a security group was not specified.
    if not security_groups:
        security_groups = [create_security_group(vpc, allowed_inbound_traffic=[('HTTP',   '0.0.0.0/0')
                                                                              ,('HTTPS',  '0.0.0.0/0')]
                                                    , allowed_outbound_traffic=[('HTTP',  '0.0.0.0/0')
                                                                               ,('HTTPS', '0.0.0.0/0')
                                                                               ,('DNS',   '0.0.0.0/0')])]

    # Create EC2 instances.
    instances = list()
    for subnet in subnets:
        instance = create_ec2_instance(subnet, security_groups, script, instance_profile_name, os, image_id)
        instances = instances + instance
    return instances

def create_ec2_instance(subnet, security_groups=None, script=None, instance_profile=None, os='ubuntu', image_id=None):
    # Set up dictionary of OSes and their associated quick-start Amazon Machine Images (AMIs).
    ami = {
        'amazon-linux': 'ami-146e2a7c',
        'redhat':       'ami-12663b7a',
        'suse':         'ami-aeb532c6',
        'ubuntu':       'ami-9a562df2',
    }

    # Connect to the Amazon Elastic Compute Cloud (Amazon EC2) service.
    ec2_connection = connect_ec2()

    # Determine whether to use a start-up AMI or a specific AMI.
    if image_id:
        image = ec2_connection.get_image(image_id)
        if not image:
            raise RuntimeError('The specified Amazon Machine Image (AMI) could not be found (%s).' % image_id)
    else:
        image_id = ami[os]

    # Generate random identifier.
    random_id = '{:08x}'.format(random.randrange(2**32))

    # Generate Elastic Network Interface (ENI) name.
    eni_name = '-'.join(['eni', core.PROJECT_NAME.lower(), core.args.environment.lower(), random_id])

    # Create Elastic Network Interface (ENI) specification.
    interface = boto.ec2.networkinterface.NetworkInterfaceSpecification(subnet_id=subnet.id,
                                                                        groups=[security_group.id for security_group in security_groups],
                                                                        associate_public_ip_address=True)
    interfaces = boto.ec2.networkinterface.NetworkInterfaceCollection(interface)

    # Create EC2 Reservation.
    ec2_instance_name = '-'.join(['ec2', core.PROJECT_NAME.lower(), core.args.environment.lower(), random_id])
    logger.info('Creating EC2 Instance (%s) in %s.' % (ec2_instance_name, subnet.availability_zone))
    reservation = ec2_connection.run_instances(image_id,                 # image_id
                                               instance_type='t2.micro',
                                               instance_profile_name=instance_profile.name if instance_profile else None,
                                               network_interfaces=interfaces,
                                               user_data=script)
    logger.info('Created EC2 Instance (%s).' % ec2_instance_name)

    # Get EC2 Instances.
    instances = [instances for instances in reservation.instances]

    # Tag EC2 Instances.
    tagged = False
    while not tagged:
        try:
            tagged = ec2_connection.create_tags([instance.id for instance in instances], {'Name': ec2_instance_name,
                                                                                          'Project': core.PROJECT_NAME.lower(),
                                                                                          'Environment': core.args.environment.lower()})
        except boto.exception.EC2ResponseError as error:
            if error.code == 'InvalidInstanceID.NotFound': # Instance hasn't registered with EC2 service yet.
                pass
            else:
                raise boto.exception.EC2ResponseError

    # Get Elastic Network Interface (ENI) attached to instances.
    interfaces = None
    while not interfaces:
        try:
            interfaces = ec2_connection.get_all_network_interfaces(filters={'attachment.instance-id': [instance.id for instance in instances]})
        except boto.exception.EC2ResponseError as error:
            if error.code == 'InvalidInstanceID.NotFound': # Instance hasn't registered with EC2 service yet.
                pass
            else:
                raise boto.exception.EC2ResponseError

    # Tag Elastic Network Interface (ENI).
    tagged = False
    while not tagged:
        try:
            tagged = ec2_connection.create_tags([interface.id for interface in interfaces], {'Name': eni_name,
                                                                                             'Project': core.PROJECT_NAME.lower(),
                                                                                             'Environment': core.args.environment.lower()})
        except boto.exception.EC2ResponseError as error:
            if error.code == 'InvalidNetworkInterfaceID.NotFound': # ENI hasn't registered with EC2 service yet.
                pass
            else:
                raise boto.exception.EC2ResponseError

    return instances

def get_nat_image(paravirtual=False):
    # Connect to the Amazon Elastic Compute Cloud (Amazon EC2) service.
    ec2_connection = connect_ec2()

    # Get paravirtual (PV) or hardware virtual machine (HVM) Amazon Linux VPC NAT AMIs.
    images = ec2_connection.get_all_images(filters={'owner-alias': 'amazon',
                                                    'name': 'amzn-ami-vpc-nat-' + ('pv' if paravirtual else 'hvm') + '*',})

    # Return the most recent AMI.
    image = sorted(images, key=lambda x: x.name.split('-')[5])[-1]
    return image

def run(script, command):
    script += '\n' + command
    return script

def install_package(script, package_name):
    script += '\n' + 'apt-get --yes --quiet install %s' % package_name
    return script