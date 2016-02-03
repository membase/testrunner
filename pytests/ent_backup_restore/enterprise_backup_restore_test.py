import re
from random import randrange

from couchbase_helper.cluster import Cluster
from couchbase_helper.documentgenerator import BlobGenerator
from ent_backup_restore.enterprise_backup_restore_base import EnterpriseBackupRestoreBase
from membase.api.rest_client import RestConnection, Bucket
from remote.remote_util import RemoteUtilHelper, RemoteMachineShellConnection
from security.auditmain import audit
from newupgradebasetest import NewUpgradeBaseTest
from couchbase.bucket import Bucket

AUDITBACKUPID = 20480
AUDITRESTOREID= 20485


class EnterpriseBackupRestoreTest(EnterpriseBackupRestoreBase, NewUpgradeBaseTest):
    def setUp(self):
        super(EnterpriseBackupRestoreTest, self).setUp()

    def tearDown(self):
        super(EnterpriseBackupRestoreTest, self).tearDown()

    def test_backup_create(self):
        self.backup_create_validate()

    def test_backup_restore_sanity(self):
        """
        1. Create default bucket on the cluster and loads it with given number of items
        2. Perform updates and create backups for specified number of times (test param number_of_backups)
        3. Perform restores for the same number of times with random start and end values
        """
        gen = BlobGenerator("ent-backup", "ent-backup-", self.value_size, end=self.num_items)
        self._load_all_buckets(self.master, gen, "create", self.expires)
        self.ops_type = self.input.param("ops-type", "update")
        if self.auto_failover:
            self.log.info("Enabling auto failover on " + str(self.backupset.cluster_host))
            rest_conn = RestConnection(self.backupset.cluster_host)
            rest_conn.update_autofailover_settings(self.auto_failover, self.auto_failover_timeout)
        self.backup_create_validate()
        for i in range(1, self.backupset.number_of_backups + 1):
            if self.ops_type == "update":
                self._load_all_buckets(self.master, gen, "update", self.expires)
            elif self.ops_type == "delete":
                self._load_all_buckets(self.master, gen, "delete", self.expires)
            self.backup_cluster_validate()
        self.targetMaster = True
        start = randrange(1, self.backupset.number_of_backups + 1)
        if start == self.backupset.number_of_backups:
            end = start
        else:
            end = randrange(start, self.backupset.number_of_backups + 1)
        restored = {"{0}/{1}".format(start, end): ""}
        for i in range(1, self.backupset.number_of_backups + 1):
            if self.reset_restore_cluster:
                self.backup_reset_clusters(self.cluster_to_restore)
                if self.same_cluster:
                    self._initialize_nodes(Cluster(), self.servers[:self.nodes_init])
                else:
                    self._initialize_nodes(Cluster(), self.input.clusters[0][:self.nodes_init])
            self.backupset.start = start
            self.backupset.end = end
            self.backup_restore_validate(compare_uuid=False, seqno_compare_function=">=")
            if self.backupset.number_of_backups == 1:
                continue
            while "{0}/{1}".format(start, end) in restored:
                start = randrange(1, self.backupset.number_of_backups + 1)
                if start == self.backupset.number_of_backups:
                    end = start
                else:
                    end = randrange(start, self.backupset.number_of_backups + 1)
            restored["{0}/{1}".format(start, end)] = ""

    def test_backup_restore_after_rebalance(self):
        """
        1. Create default bucket on the cluster and loads it with given number of items
        2. Does a rebalance on cluster to be backed up with specified number of servers in (test param nodes_in) and
        servers out (test param nodes_out)
        3. Takes a backup
        4. Does a rebalance on cluster to be restored to with specified number of servers in (test param nodes_in) and
        servers out (test param nodes_out)
        5. Performs a restore on the restore cluster
        """
        serv_in = self.servers[self.nodes_init:self.nodes_init + self.nodes_in]
        serv_out = self.servers[self.nodes_init - self.nodes_out:self.nodes_init]
        gen = BlobGenerator("ent-backup", "ent-backup-", self.value_size, end=self.num_items)
        self._load_all_buckets(self.master, gen, "create", 0)
        self.backup_create_validate()
        self.backupset.number_of_backups = 1
        rebalance = self.cluster.async_rebalance(self.cluster_to_backup, serv_in, serv_out)
        rebalance.result()
        self.backup_cluster_validate()
        if not self.same_cluster:
            self._initialize_nodes(Cluster(), self.input.clusters[0][:self.nodes_init])
            serv_in = self.input.clusters[0][self.nodes_init: self.nodes_init + self.nodes_in]
            serv_out = self.input.clusters[0][self.nodes_init - self.nodes_out: self.nodes_init]
            rebalance = self.cluster.async_rebalance(self.cluster_to_restore, serv_in, serv_out)
        else:
            rebalance = self.cluster.async_rebalance(self.cluster_to_restore, serv_out, serv_in)
        rebalance.result()
        self.backup_restore_validate(compare_uuid=False, seqno_compare_function="<=")

    def test_backup_restore_with_rebalance(self):
        """
        1. Create default bucket on the cluster and loads it with given number of items
        2. Does a rebalance on cluster to be backed up with specified number of servers in (test param nodes_in) and
        servers out (test param nodes_out)
        3. Takes a backup while rebalance is going on
        4. Does a rebalance on cluster to be restored to with specified number of servers in (test param nodes_in) and
        servers out (test param nodes_out)
        5. Performs a restore on the restore cluster while rebalance is going on
        """
        serv_in = self.servers[self.nodes_init:self.nodes_init + self.nodes_in]
        serv_out = self.servers[self.nodes_init - self.nodes_out:self.nodes_init]
        gen = BlobGenerator("ent-backup", "ent-backup-", self.value_size, end=self.num_items)
        self._load_all_buckets(self.master, gen, "create", 0)
        self.backup_create_validate()
        self.backupset.number_of_backups = 1
        self.cluster.async_rebalance(self.cluster_to_backup, serv_in, serv_out)
        self.sleep(10)
        self.backup_cluster_validate()
        if not self.same_cluster:
            self._initialize_nodes(Cluster(), self.input.clusters[0][:self.nodes_init])
            serv_in = self.input.clusters[0][self.nodes_init: self.nodes_init + self.nodes_in]
            serv_out = self.input.clusters[0][self.nodes_init - self.nodes_out: self.nodes_init]
            self.cluster.async_rebalance(self.cluster_to_restore, serv_in, serv_out)
        else:
            self.cluster.async_rebalance(self.cluster_to_restore, serv_out, serv_in)
        self.sleep(10)
        self.backup_restore_validate(compare_uuid=False, seqno_compare_function="<=")

    def test_backup_restore_with_ops(self):
        """
        1. Create default bucket on the cluster and loads it with given number of items
        2. Perform the specified ops (test param ops-type) and create backups for specified number of times
        (test param number_of_backups)
        3. Perform restores for the same number of times with random start and end values
        """
        gen = BlobGenerator("ent-backup", "ent-backup-", self.value_size, end=self.num_items)
        self._load_all_buckets(self.master, gen, "create", 0)
        self.ops_type = self.input.param("ops-type", "update")
        self.backup_create()
        for i in range(1, self.backupset.number_of_backups + 1):
            self._backup_restore_with_ops()
        start = randrange(1, self.backupset.number_of_backups + 1)
        if start == self.backupset.number_of_backups:
            end = start
        else:
            end = randrange(start, self.backupset.number_of_backups + 1)
        restored = {"{0}/{1}".format(start, end): ""}
        for i in range(1, self.backupset.number_of_backups + 1):
            self.backupset.start = start
            self.backupset.end = end
            self._backup_restore_with_ops(backup=False)
            if self.backupset.number_of_backups == 1:
                continue
            while "{0}/{1}".format(start, end) in restored:
                start = randrange(1, self.backupset.number_of_backups + 1)
                if start == self.backupset.number_of_backups:
                    end = start
                else:
                    end = randrange(start, self.backupset.number_of_backups + 1)
            restored["{0}/{1}".format(start, end)] = ""

    def _backup_restore_with_ops(self, exp=0, backup=True, compare_uuid=False, compare_function="==", replicas=False,
                                 mode="memory"):
        self.ops_type = self.input.param("ops-type", "update")
        gen = BlobGenerator("ent-backup", "ent-backup-", self.value_size, end=self.num_items)
        self._load_all_buckets(self.master, gen, self.ops_type, exp)
        if backup:
            self.backup_cluster_validate()
        else:
            self.backup_restore_validate(compare_uuid=compare_uuid, seqno_compare_function=compare_function,
                                         replicas=replicas, mode=mode)

    def test_backup_list(self):
        """
        1. Creates specified bucket on the cluster and loads it with given number of items
        2. Creates a backup and validates it
        3. Executes list command on the backupset and validates the output
        """
        gen = BlobGenerator("ent-backup", "ent-backup-", self.value_size, end=self.num_items)
        self._load_all_buckets(self.master, gen, "create", 0)
        self.backup_create()
        self.backup_cluster_validate()
        self.backup_list_validate()

    def test_backup_list_optional_switches(self):
        """
        1. Creates specified buckets on the cluster and loads it with given number of items
           Note: this test should be run with 2 buckets
        2. Creates two backupsets
        3. Creates two backups on each of the backupset
        4. Executes list command with --name and validates
        5. Executes list command with --name and --incr-backup and validates
        6. Executes list command with --name, --incr-backup and --bucket-backup and validates
        """
        gen = BlobGenerator("ent-backup", "ent-backup-", self.value_size, end=self.num_items)
        self._load_all_buckets(self.master, gen, "create", 0)
        self.backup_create()
        self._take_n_backups(n=2)
        self.backupset.name = "backup2"
        self.backup_create()
        self._take_n_backups(n=2)
        incr_names = 0
        backup_name = False
        self.backupset.backup_list_name = "backup"
        status, output, message = self.backup_list()
        if not status:
            self.fail(message)
        for line in output:
            if self.backupset.backup_list_name in line:
                backup_name = True
            if self.backups[0] in line:
                incr_names += 1
            if self.backups[1] in line:
                incr_names += 1
        self.assertTrue(backup_name, "Expected backup name not found in output")
        self.log.info("Expected backup name found in output")
        self.assertEqual(incr_names, 2, "Expected backups were not listed for --name option")
        self.log.info("Expected backups listed for --name option")
        incr_names = 0
        backup_name = False
        self.backupset.backup_list_name = "backup2"
        status, output, message = self.backup_list()
        if not status:
            self.fail(message)
        for line in output:
            if self.backupset.backup_list_name in line:
                backup_name = True
            if self.backups[2] in line:
                incr_names += 1
            if self.backups[3] in line:
                incr_names += 1
        self.assertTrue(backup_name, "Expected backup name not found in output")
        self.log.info("Expected backup name found in output")
        self.assertEqual(incr_names, 2, "Expected backups were not listed for --name option")
        self.log.info("Expected backups listed for --name option")
        buckets = 0
        name = False
        self.backupset.backup_list_name = "backup"
        self.backupset.backup_incr_backup = self.backups[0]
        status, output, message = self.backup_list()
        if not status:
            self.fail(message)
        for line in output:
            if self.backupset.backup_incr_backup in line:
                name = True
            if self.buckets[0].name in line:
                buckets += 1
            if self.buckets[1].name in line:
                buckets += 1
        self.assertTrue(name, "Expected incremental backup name not found in output")
        self.log.info("Expected incrmental backup name found in output")
        self.assertEqual(buckets, 2, "Expected buckets were not listed for --incr-backup option")
        self.log.info("Expected buckets were listed for --incr-backup option")
        name = False
        items = 0
        self.backupset.backup_list_name = "backup2"
        self.backupset.backup_incr_backup = self.backups[2]
        self.backupset.bucket_backup = self.buckets[0].name
        status, output, message = self.backup_list()
        if not status:
            self.fail(message)
        for line in output:
            if self.buckets[0].name in line:
                name = True
            if "shard" in line:
                split = line.split(" ")
                split = [s for s in split if s]
                items += int(split[1])
        self.assertTrue(name, "Expected bucket not listed for --bucket-backup option")
        self.log.info("Expected bucket listed for --bucket-backup option")
        self.assertEqual(items, self.num_items, "Mismatch in items for --bucket-backup option")
        self.log.info("Expected number of items for --bucket-backup option")

    def _take_n_backups(self, n=1, validate=False):
        for i in range(1, n + 1):
            if validate:
                self.backup_cluster_validate()
            else:
                self.backup_cluster()

    def test_backup_compact(self):
        """
        1. Creates specified bucket on the cluster and loads it with given number of items
        2. Creates a backup and validates it
        3. Executes compact command on the backupset and validates the output
        """
        gen = BlobGenerator("ent-backup", "ent-backup-", self.value_size, end=self.num_items)
        self._load_all_buckets(self.master, gen, "create", 0)
        self.backup_create()
        self.backup_cluster_validate()
        self.backup_compact_validate()

    def test_restore_from_compacted_backup(self):
        """
        1. Creates specified bucket on the cluster and loads it with given number of items
        2. Creates a backup and validates it
        3. Executes compact command on the backupset
        4. Restores from the compacted backup and validates it
        """
        gen = BlobGenerator("ent-backup", "ent-backup-", self.value_size, end=self.num_items)
        self._load_all_buckets(self.master, gen, "create", 0)
        self.backup_create()
        self.backup_cluster_validate()
        self.backup_compact()
        self.backup_restore_validate()

    def test_backup_restore_with_nodes_reshuffle(self):
        """
        1. Creates specified bucket on the cluster and loads it with given number of items
        2. Enlists the default zone of current cluster - backsup the cluster and validates
        3. Creates a new zone - shuffles cluster host to new zone
        4. Restores to cluster host and validates
        """
        gen = BlobGenerator("ent-backup", "ent-backup-", self.value_size, end=self.num_items)
        self._load_all_buckets(self.master, gen, "create", 0)
        rest_conn = RestConnection(self.backupset.cluster_host)
        zones = rest_conn.get_zone_names().keys()
        source_zone = zones[0]
        target_zone = "test_backup_restore"
        self.log.info("Current nodes in group {0} : {1}".format(source_zone,
                                                                str(rest_conn.get_nodes_in_zone(source_zone).keys())))
        self.log.info("Taking backup with current groups setup")
        self.backup_create()
        self.backup_cluster_validate()
        self.log.info("Creating new zone " + target_zone)
        rest_conn.add_zone(target_zone)
        self.log.info("Moving {0} to new zone {1}".format(self.backupset.cluster_host.ip, target_zone))
        rest_conn.shuffle_nodes_in_zones(["{0}".format(self.backupset.cluster_host.ip)],source_zone,target_zone)
        self.log.info("Restoring to {0} after group change".format(self.backupset.cluster_host.ip))
        self.backup_restore_validate()
        self.log.info("Moving {0} back to old zone {1}".format(self.backupset.cluster_host.ip, source_zone))
        rest_conn.shuffle_nodes_in_zones(["{0}".format(self.backupset.cluster_host.ip)],target_zone,source_zone)
        self.log.info("Deleting new zone " + target_zone)
        rest_conn.delete_zone(target_zone)

    def test_backup_restore_with_firewall(self):
        """
        1. Creates specified bucket on the cluster and loads it with given number of items
        2. Creates backupset on backup host
        3. Enables firewall on cluster host and validates if backup cluster command throws expected error
        4. Disables firewall on cluster host, takes backup and validates
        5. Enables firewall on restore host and validates if backup restore command throws expected error
        6. Disables firewall on restore host, restores and validates
        """
        gen = BlobGenerator("ent-backup", "ent-backup-", self.value_size, end=self.num_items)
        self._load_all_buckets(self.master, gen, "create", 0)
        self.backup_create()
        self.log.info("Enabling firewall on cluster host before backup")
        RemoteUtilHelper.enable_firewall(self.backupset.cluster_host)
        output, error = self.backup_cluster()
        self.assertTrue("getsockopt: connection refused" in output[0],
                        "Expected error not thrown by backup cluster when firewall enabled")
        self.log.info("Disabling firewall on cluster host to take backup")
        conn = RemoteMachineShellConnection(self.backupset.cluster_host)
        conn.disable_firewall()
        self.log.info("Trying backup now")
        self.backup_cluster_validate()
        self.log.info("Enabling firewall on restore host before restore")
        RemoteUtilHelper.enable_firewall(self.backupset.restore_cluster_host)
        output, error = self.backup_restore()
        self.assertTrue("getsockopt: connection refused" in output[0],
                       "Expected error not thrown by backup restore when firewall enabled")
        self.log.info("Disabling firewall on restore host to restore")
        conn = RemoteMachineShellConnection(self.backupset.restore_cluster_host)
        conn.disable_firewall()
        self.log.info("Trying restore now")
        self.backup_restore_validate()

    def test_backup_restore_with_audit(self):
        """
        1. Creates specified bucket on the cluster and loads it with given number of items
        2. Creates backupset on backup host
        3. Creates a backup of the cluster host - verifies if corresponding entry was created in audit log
        4. Restores data on to restore host - verifies if corresponding entry was created in audit log
        """
        gen = BlobGenerator("ent-backup", "ent-backup-", self.value_size, end=self.num_items)
        self._load_all_buckets(self.master, gen, "create", 0)
        audit_obj = audit(AUDITBACKUPID, self.backupset.cluster_host)
        status = audit_obj.getAuditStatus()
        self.log.info("Audit status on {0} is {1}".format(self.backupset.cluster_host.ip, status))
        if not status:
            self.log.info("Enabling audit on {0}".format(self.backupset.cluster_host.ip))
            audit_obj.setAuditEnable('true')
        self.backup_create()
        self.backup_cluster()
        field_verified, value_verified = audit_obj.validateEvents(self._get_event_expected_results(action='backup'))
        self.assertTrue(field_verified, "One of the fields is not matching")
        self.assertTrue(value_verified, "Values for one of the fields is not matching")
        audit_obj = audit(AUDITBACKUPID, self.backupset.restore_cluster_host)
        status = audit_obj.getAuditStatus()
        self.log.info("Audit status on {0} is {1}".format(self.backupset.restore_cluster_host.ip, status))
        if not status:
            self.log.info("Enabling audit on {0}".format(self.backupset.restore_cluster_host.ip))
            audit_obj.setAuditEnable('true')
        self.backup_restore()
        audit_obj = audit(AUDITRESTOREID, self.backupset.restore_cluster_host)
        field_verified, value_verified = audit_obj.validateEvents(self._get_event_expected_results(action='restore'))
        self.assertTrue(field_verified, "One of the fields is not matching")
        self.assertTrue(value_verified, "Values for one of the fields is not matching")

    def _get_event_expected_results(self, action):
        if action == 'backup':
            expected_results = {
                "real_userid:source": "memcached",
                "real_userid:user": "default",
                "name": "opened DCP connection",
                "id": AUDITBACKUPID,
                "description": "opened DCP connection",
                "timestamp": "{0}".format(self.backups[0]),
                "bucket": "{0}".format(self.buckets[0].name),
                "sockname": "{0}:11210".format(self.backupset.cluster_host.ip),
                "peername": "{0}".format(self.backupset.backup_host.ip)
                }
        elif action == 'restore':
            expected_results = {
                "real_userid:source": "memcached",
                "real_userid:user": "unknown",
                "name": "authentication succeeded",
                "id": AUDITRESTOREID,
                "description": "Authentication to the cluster succeeded",
                "timestamp": "{0}".format(self.backups[0]),
                "bucket": "{0}".format(self.buckets[0].name),
                "sockname": "{0}:11210".format(self.backupset.restore_cluster_host.ip),
                "peername": "{0}".format(self.backupset.backup_host.ip)
                }
        return expected_results

    def test_backup_restore_with_lesser_nodes(self):
        """
        1. Creates specified bucket on the cluster and loads it with given number of items
        2. Adds another node to restore cluster and rebalances - note the test has to be run with nodes_init >= 3 so
           that cluster host had more nodes than restore host
        3. Creates backupset on backup host
        4. Creates backup of cluster host with 3 or more number of nodes and validates
        5. Restores to restore host with lesser number of nodes (2) and validates
        """
        gen = BlobGenerator("ent-backup", "ent-backup-", self.value_size, end=self.num_items)
        self._load_all_buckets(self.master, gen, "create", 0)
        rest_conn = RestConnection(self.backupset.restore_cluster_host)
        rest_conn.add_node(self.input.clusters[0][1].rest_username, self.input.clusters[0][1].rest_password,
                           self.input.clusters[0][1].ip)
        rebalance = self.cluster.async_rebalance(self.cluster_to_restore, [], [])
        rebalance.result()
        self.backup_create()
        self.backup_cluster_validate()
        self.backup_restore_validate()

    def test_backup_with_full_disk(self):
        """
        Things to be done before running this testcase:
            - scripts/install.py has to be run with init_nodes=False
            - scripts/cbqe3043.py has to be run against the ini file - this script will mount a 20MB partition on the
              nodes required for the test
        1. Creates specified bucket on the cluster and loads it with given number of items
        2. Creates a bucket on the backup host and pumps it with 50000 items so that 20MB disk is almost full
        3. Sets backup directory to the 20MB partition and creates a backupset
        4. Keeps taking backup until no space left on device error is hit
        """
        gen = BlobGenerator("ent-backup", "ent-backup-", self.value_size, end=self.num_items)
        self._load_all_buckets(self.master, gen, "create", 0)
        rest_conn = RestConnection(self.backupset.backup_host)
        rest_conn.create_bucket(bucket="default",ramQuotaMB=512)
        gen = BlobGenerator("ent-backup", "ent-backup-", self.value_size, end=50000)
        self._load_all_buckets(self.backupset.backup_host, gen, "create", 0)
        self.backupset.directory = "/cbqe3043/entbackup"
        self.backup_create()
        output, error = self.backup_cluster()
        while "Backup successfully completed" in output[0]:
            output, error = self.backup_cluster()
        self.assertTrue("no space left on device" in output[0],
                        "Expected error message not thrown by backup when disk is full")
        self.log.info("Expected no space left on device error thrown by backup command")

    def test_backup_and_restore_with_memcached_buckets(self):
        """
        1. Creates specified buckets on the cluster and loads it with given number of items- memcached bucket has to
           be created for this test (memcached_buckets=1)
        2. Creates a backupset, takes backup of the cluster host and validates
        3. Executes list command on the backup and validates that memcached bucket has been skipped
        4. Restores the backup and validates
        """
        gen = BlobGenerator("ent-backup", "ent-backup-", self.value_size, end=self.num_items)
        self._load_all_buckets(self.master, gen, "create", 0)
        self.backup_create()
        self.backup_cluster()
        status, output, message = self.backup_list()
        if not status:
            self.fail("Getting backup list to validate memcached buckets failed.")
        for line in output:
            self.assertTrue("memcached_bucket0" not in line,
                            "Memcached bucket found in backup list output after backup")
        self.log.info("Memcached bucket not found in backup list output after backup as expected")
        self.backup_restore()

    def test_backup_with_erlang_crash_and_restart(self):
        """
        1. Creates specified bucket on the cluster and loads it with given number of items
        2. Creates a backupset on the backup host
        3. Initiates a backup - while backup is going on kills and restarts erlang process
        4. Validates backup output
        """
        gen = BlobGenerator("ent-backup", "ent-backup-", self.value_size, end=self.num_items)
        self._load_all_buckets(self.master, gen, "create", 0)
        self.backup_create()
        backup_result = self.cluster.async_backup_cluster(cluster_host=self.backupset.cluster_host,
                                          backup_host=self.backupset.backup_host,
                                          directory=self.backupset.directory, name=self.backupset.name,
                                          resume=self.backupset.resume, purge=self.backupset.purge,
                                          no_progress_bar=self.no_progress_bar,
                                          cli_command_location=self.cli_command_location)
        self.sleep(10)
        conn = RemoteMachineShellConnection(self.backupset.cluster_host)
        conn.kill_erlang()
        conn.start_couchbase()
        output = backup_result.result(timeout=200)
        self.assertTrue("Backup successfully completed" in output[0],
                        "Backup failed with erlang crash and restart within 180 seconds")
        self.log.info("Backup succeeded with erlang crash and restart within 180 seconds")

    def test_backup_with_couchbase_stop_and_start(self):
        """
        1. Creates specified bucket on the cluster and loads it with given number of items
        2. Creates a backupset on the backup host
        3. Initiates a backup - while backup is going on kills and restarts couchbase server
        4. Validates backup output
        """
        gen = BlobGenerator("ent-backup", "ent-backup-", self.value_size, end=self.num_items)
        self._load_all_buckets(self.master, gen, "create", 0)
        self.backup_create()
        backup_result = self.cluster.async_backup_cluster(cluster_host=self.backupset.cluster_host,
                                          backup_host=self.backupset.backup_host,
                                          directory=self.backupset.directory, name=self.backupset.name,
                                          resume=self.backupset.resume, purge=self.backupset.purge,
                                          no_progress_bar=self.no_progress_bar,
                                          cli_command_location=self.cli_command_location)
        self.sleep(10)
        conn = RemoteMachineShellConnection(self.backupset.cluster_host)
        conn.stop_couchbase()
        conn.start_couchbase()
        output = backup_result.result(timeout=200)
        self.assertTrue("Backup successfully completed" in output[0],
                        "Backup failed with couchbase stop and start within 180 seconds")
        self.log.info("Backup succeeded with couchbase stop and start within 180 seconds")

    def test_backup_with_memcached_crash_and_restart(self):
        """
        1. Creates specified bucket on the cluster and loads it with given number of items
        2. Creates a backupset on the backup host
        3. Initiates a backup - while backup is going on kills and restarts memcached process
        4. Validates backup output
        """
        gen = BlobGenerator("ent-backup", "ent-backup-", self.value_size, end=self.num_items)
        self._load_all_buckets(self.master, gen, "create", 0)
        self.backup_create()
        backup_result = self.cluster.async_backup_cluster(cluster_host=self.backupset.cluster_host,
                                          backup_host=self.backupset.backup_host,
                                          directory=self.backupset.directory, name=self.backupset.name,
                                          resume=self.backupset.resume, purge=self.backupset.purge,
                                          no_progress_bar=self.no_progress_bar,
                                          cli_command_location=self.cli_command_location)
        self.sleep(10)
        conn = RemoteMachineShellConnection(self.backupset.cluster_host)
        conn.pause_memcached()
        conn.unpause_memcached()
        output = backup_result.result(timeout=200)
        self.assertTrue("Backup successfully completed" in output[0],
                        "Backup failed with memcached crash and restart within 180 seconds")
        self.log.info("Backup succeeded with memcached crash and restart within 180 seconds")

    def test_backup_with_erlang_crash(self):
        """
        1. Creates specified bucket on the cluster and loads it with given number of items
        2. Creates a backupset on the backup host
        3. Initiates a backup - while backup is going on kills erlang process
        4. Waits for 200s and Validates backup error
        """
        gen = BlobGenerator("ent-backup", "ent-backup-", self.value_size, end=self.num_items)
        self._load_all_buckets(self.master, gen, "create", 0)
        self.backup_create()
        try:
            backup_result = self.cluster.async_backup_cluster(cluster_host=self.backupset.cluster_host,
                                                              backup_host=self.backupset.backup_host,
                                                              directory=self.backupset.directory,
                                                              name=self.backupset.name,
                                                              resume=self.backupset.resume, purge=self.backupset.purge,
                                                              no_progress_bar=self.no_progress_bar,
                                                              cli_command_location=self.cli_command_location)
            self.sleep(10)
            conn = RemoteMachineShellConnection(self.backupset.cluster_host)
            conn.kill_erlang()
            output = backup_result.result(timeout=200)
            self.assertTrue("Error backing up cluster: Not all data was backed up due to connectivity issues." in output[0],
                            "Expected error message not thrown by Backup 180 seconds after erlang crash")
            self.log.info("Expected error message thrown by Backup 180 seconds after erlang crash")
        except Exception as ex:
            self.log.info(str(ex))
        finally:
            conn.start_couchbase()
            self.sleep(30)

    def test_backup_with_couchbase_stop(self):
        """
        1. Creates specified bucket on the cluster and loads it with given number of items
        2. Creates a backupset on the backup host
        3. Initiates a backup - while backup is going on kills couchbase server
        4. Waits for 200s and Validates backup error
        """
        gen = BlobGenerator("ent-backup", "ent-backup-", self.value_size, end=self.num_items)
        self._load_all_buckets(self.master, gen, "create", 0)
        self.backup_create()
        try:
            backup_result = self.cluster.async_backup_cluster(cluster_host=self.backupset.cluster_host,
                                                              backup_host=self.backupset.backup_host,
                                                              directory=self.backupset.directory,
                                                              name=self.backupset.name,
                                                              resume=self.backupset.resume, purge=self.backupset.purge,
                                                              no_progress_bar=self.no_progress_bar,
                                                              cli_command_location=self.cli_command_location)
            self.sleep(10)
            conn = RemoteMachineShellConnection(self.backupset.cluster_host)
            conn.stop_couchbase()
            output = backup_result.result(timeout=200)
            self.assertTrue("Error backing up cluster: Not all data was backed up due to connectivity issues." in output[0],
                            "Expected error message not thrown by Backup 180 seconds after couchbase-server stop")
            self.log.info("Expected error message thrown by Backup 180 seconds after couchbase-server stop")
        except Exception as ex:
            self.log.info(str(ex))
        finally:
            conn.start_couchbase()
            self.sleep(30)

    def test_backup_with_memcached_crash(self):
        """
        1. Creates specified bucket on the cluster and loads it with given number of items
        2. Creates a backupset on the backup host
        3. Initiates a backup - while backup is going on kills memcached process
        4. Waits for 200s and Validates backup error
        """
        gen = BlobGenerator("ent-backup", "ent-backup-", self.value_size, end=self.num_items)
        self._load_all_buckets(self.master, gen, "create", 0)
        self.backup_create()
        try:
            backup_result = self.cluster.async_backup_cluster(cluster_host=self.backupset.cluster_host,
                                                              backup_host=self.backupset.backup_host,
                                                              directory=self.backupset.directory,
                                                              name=self.backupset.name,
                                                              resume=self.backupset.resume, purge=self.backupset.purge,
                                                              no_progress_bar=self.no_progress_bar,
                                                              cli_command_location=self.cli_command_location)
            self.sleep(10)
            conn = RemoteMachineShellConnection(self.backupset.cluster_host)
            conn.pause_memcached()
            output = backup_result.result(timeout=200)
            self.assertTrue("Error backing up cluster: Not all data was backed up due to connectivity issues." in output[0],
                            "Expected error message not thrown by Backup 180 seconds after memcached crash")
            self.log.info("Expected error message thrown by Backup 180 seconds after memcached crash")
        except Exception as ex:
            self.log.info(str(ex))
        finally:
            conn.unpause_memcached()
            self.sleep(30)

    def test_restore_with_erlang_crash_and_restart(self):
        """
        1. Creates specified bucket on the cluster and loads it with given number of items
        2. Creates a backupset on the backup host and backsup data
        3. Initiates a restore - while restore is going on kills and restarts erlang process
        4. Validates restore output
        """
        gen = BlobGenerator("ent-backup", "ent-backup-", self.value_size, end=self.num_items)
        self._load_all_buckets(self.master, gen, "create", 0)
        self.backup_create()
        self.backup_cluster()
        restore_result = self.cluster.async_restore_cluster(restore_host=self.backupset.restore_cluster_host,
                                                           backup_host=self.backupset.backup_host,
                                                           backups=self.backups, start=self.backupset.start,
                                                           end=self.backupset.end, directory=self.backupset.directory,
                                                           name=self.backupset.name,
                                                           force_updates=self.backupset.force_updates,
                                                           no_progress_bar=self.no_progress_bar,
                                                           cli_command_location=self.cli_command_location)
        self.sleep(10)
        conn = RemoteMachineShellConnection(self.backupset.restore_cluster_host)
        conn.kill_erlang()
        conn.start_couchbase()
        output = restore_result.result(timeout=200)
        self.assertTrue("Restore completed successfully" in output[0],
                        "Restore failed with erlang crash and restart within 180 seconds")
        self.log.info("Restore succeeded with erlang crash and restart within 180 seconds")

    def test_restore_with_couchbase_stop_and_start(self):
        """
        1. Creates specified bucket on the cluster and loads it with given number of items
        2. Creates a backupset on the backup host and backsup data
        3. Initiates a restore - while restore is going on kills and restarts couchbase process
        4. Validates restore output
        """
        gen = BlobGenerator("ent-backup", "ent-backup-", self.value_size, end=self.num_items)
        self._load_all_buckets(self.master, gen, "create", 0)
        self.backup_create()
        self.backup_cluster()
        restore_result = self.cluster.async_restore_cluster(restore_host=self.backupset.restore_cluster_host,
                                                           backup_host=self.backupset.backup_host,
                                                           backups=self.backups, start=self.backupset.start,
                                                           end=self.backupset.end, directory=self.backupset.directory,
                                                           name=self.backupset.name,
                                                           force_updates=self.backupset.force_updates,
                                                           no_progress_bar=self.no_progress_bar,
                                                           cli_command_location=self.cli_command_location)
        self.sleep(10)
        conn = RemoteMachineShellConnection(self.backupset.restore_cluster_host)
        conn.stop_couchbase()
        conn.start_couchbase()
        output = restore_result.result(timeout=200)
        self.assertTrue("Restore completed successfully" in output[0],
                        "Restore failed with couchbase stop and start within 180 seconds")
        self.log.info("Restore succeeded with couchbase stop and start within 180 seconds")

    def test_restore_with_memcached_crash_and_restart(self):
        """
        1. Creates specified bucket on the cluster and loads it with given number of items
        2. Creates a backupset on the backup host and backsup data
        3. Initiates a restore - while restore is going on kills and restarts memcached process
        4. Validates restore output
        """
        gen = BlobGenerator("ent-backup", "ent-backup-", self.value_size, end=self.num_items)
        self._load_all_buckets(self.master, gen, "create", 0)
        self.backup_create()
        self.backup_cluster()
        restore_result = self.cluster.async_restore_cluster(restore_host=self.backupset.restore_cluster_host,
                                                           backup_host=self.backupset.backup_host,
                                                           backups=self.backups, start=self.backupset.start,
                                                           end=self.backupset.end, directory=self.backupset.directory,
                                                           name=self.backupset.name,
                                                           force_updates=self.backupset.force_updates,
                                                           no_progress_bar=self.no_progress_bar,
                                                           cli_command_location=self.cli_command_location)
        self.sleep(10)
        conn = RemoteMachineShellConnection(self.backupset.restore_cluster_host)
        conn.pause_memcached()
        conn.unpause_memcached()
        output = restore_result.result(timeout=200)
        self.assertTrue("Restore completed successfully" in output[0],
                        "Restore failed with memcached crash and restart within 180 seconds")
        self.log.info("Restore succeeded with memcached crash and restart within 180 seconds")

    def test_restore_with_erlang_crash(self):
        """
        1. Creates specified bucket on the cluster and loads it with given number of items
        2. Creates a backupset on the backup host and backsup data
        3. Initiates a restore - while restore is going on kills erlang process
        4. Waits for 200s and Validates restore output
        """
        gen = BlobGenerator("ent-backup", "ent-backup-", self.value_size, end=self.num_items)
        self._load_all_buckets(self.master, gen, "create", 0)
        self.backup_create()
        self.backup_cluster()
        try:
            restore_result = self.cluster.async_restore_cluster(restore_host=self.backupset.restore_cluster_host,
                                                                backup_host=self.backupset.backup_host,
                                                                backups=self.backups, start=self.backupset.start,
                                                                end=self.backupset.end,
                                                                directory=self.backupset.directory,
                                                                name=self.backupset.name,
                                                                force_updates=self.backupset.force_updates,
                                                                no_progress_bar=self.no_progress_bar,
                                                                cli_command_location=self.cli_command_location)
            self.sleep(10)
            conn = RemoteMachineShellConnection(self.backupset.restore_cluster_host)
            conn.kill_erlang()
            output = restore_result.result(timeout=200)
            self.assertTrue("Error restoring cluster: Not all data was backed up due to connectivity issues." in output[0],
                            "Expected error message not thrown by Restore 180 seconds after erlang crash")
            self.log.info("Expected error message thrown by Restore 180 seconds after erlang crash")
        except Exception as ex:
            self.log.info(str(ex))
        finally:
            conn.start_couchbase()
            self.sleep(30)

    def test_restore_with_couchbase_stop(self):
        """
        1. Creates specified bucket on the cluster and loads it with given number of items
        2. Creates a backupset on the backup host and backsup data
        3. Initiates a restore - while restore is going on kills couchbase server
        4. Waits for 200s and Validates restore output
        """
        gen = BlobGenerator("ent-backup", "ent-backup-", self.value_size, end=self.num_items)
        self._load_all_buckets(self.master, gen, "create", 0)
        self.backup_create()
        self.backup_cluster()
        try:
            restore_result = self.cluster.async_restore_cluster(restore_host=self.backupset.restore_cluster_host,
                                                                backup_host=self.backupset.backup_host,
                                                                backups=self.backups, start=self.backupset.start,
                                                                end=self.backupset.end,
                                                                directory=self.backupset.directory,
                                                                name=self.backupset.name,
                                                                force_updates=self.backupset.force_updates,
                                                                no_progress_bar=self.no_progress_bar,
                                                                cli_command_location=self.cli_command_location)
            self.sleep(10)
            conn = RemoteMachineShellConnection(self.backupset.restore_cluster_host)
            conn.stop_couchbase()
            output = restore_result.result(timeout=200)
            self.assertTrue("Error restoring cluster: Not all data was backed up due to connectivity issues." in output[0],
                            "Expected error message not thrown by Restore 180 seconds after couchbase-server stop")
            self.log.info("Expected error message thrown by Restore 180 seconds after couchbase-server stop")
        except Exception as ex:
            self.log.info(str(ex))
        finally:
            conn.start_couchbase()
            self.sleep(30)

    def test_restore_with_memcached_crash(self):
        """
        1. Creates specified bucket on the cluster and loads it with given number of items
        2. Creates a backupset on the backup host and backsup data
        3. Initiates a restore - while restore is going on kills memcached process
        4. Waits for 200s and Validates restore output
        """
        gen = BlobGenerator("ent-backup", "ent-backup-", self.value_size, end=self.num_items)
        self._load_all_buckets(self.master, gen, "create", 0)
        self.backup_create()
        self.backup_cluster()
        try:
            restore_result = self.cluster.async_restore_cluster(restore_host=self.backupset.restore_cluster_host,
                                                                backup_host=self.backupset.backup_host,
                                                                backups=self.backups, start=self.backupset.start,
                                                                end=self.backupset.end,
                                                                directory=self.backupset.directory,
                                                                name=self.backupset.name,
                                                                force_updates=self.backupset.force_updates,
                                                                no_progress_bar=self.no_progress_bar,
                                                                cli_command_location=self.cli_command_location)
            self.sleep(10)
            conn = RemoteMachineShellConnection(self.backupset.restore_cluster_host)
            conn.pause_memcached()
            output = restore_result.result(timeout=200)
            self.assertTrue("Error restoring cluster: Not all data was backed up due to connectivity issues." in output[0],
                            "Expected error message not thrown by Restore 180 seconds after memcached crash")
            self.log.info("Expected error message thrown by Restore 180 seconds after memcached crash")
        except Exception as ex:
            self.log.info(str(ex))
        finally:
            conn.unpause_memcached()
            self.sleep(30)

    def test_backup_merge(self):
        """
        1. Creates specified bucket on the cluster and loads it with given number of items
        2. Takes specified number of backups (param number_of_backups - should be atleast 2 for this test case)
        3. Executes list command and validates if all backups are present
        4. Randomly selects a start and end and merges the backups
        5. Executes list command again and validates if the new merges set of backups are listed
        """
        gen = BlobGenerator("ent-backup", "ent-backup-", self.value_size, end=self.num_items)
        self._load_all_buckets(self.master, gen, "create", 0)
        self.backup_create()
        self._take_n_backups(n=self.backupset.number_of_backups)
        status, output, message = self.backup_list()
        if not status:
            self.fail(message)
        backup_count = 0
        for line in output:
            if re.search("\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}.\d{9}Z", line):
                backup_name = re.search("\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}.\d{9}Z", line).group()
                if backup_name in self.backups:
                    backup_count += 1
                    self.log.info("{0} matched in list command output".format(backup_name))
        self.assertEqual(backup_count, len(self.backups), "Initial number of backups did not match")
        self.log.info("Initial number of backups matched")
        self.backupset.start = randrange(1, self.backupset.number_of_backups)
        self.backupset.end = randrange(self.backupset.start + 1, self.backupset.number_of_backups + 1)
        status, output, message = self.backup_merge()
        if not status:
            self.fail(message)
        status, output, message = self.backup_list()
        if not status:
            self.fail(message)
        backup_count = 0
        for line in output:
            if re.search("\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}.\d{9}Z", line):
                backup_name = re.search("\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}.\d{9}Z", line).group()
                if backup_name in self.backups:
                    backup_count += 1
                    self.log.info("{0} matched in list command output".format(backup_name))
        self.assertEqual(backup_count, len(self.backups), "Merged number of backups did not match")
        self.log.info("Merged number of backups matched")

    def test_backup_merge_with_restore(self):
        """
        1. Creates specified bucket on the cluster and loads it with given number of items
        2. Takes two backups - restores from the backups and validates
        3. Merges both the backups - restores from merged backup and validates
        """
        gen = BlobGenerator("ent-backup", "ent-backup-", self.value_size, end=self.num_items)
        self._load_all_buckets(self.master, gen, "create", 0)
        self.backup_create()
        self._take_n_backups(n=2)
        self.backupset.start = 1
        self.backupset.end = 2
        output, error = self.backup_restore()
        if error:
            self.fail("Restoring backup failed")
        self.log.info("Finished restoring backup before merging")
        status, output, message = self.backup_merge()
        if not status:
            self.fail(message)
        self.backupset.start = 1
        self.backupset.end = 1
        output, error = self.backup_restore()
        if error:
            self.fail("Restoring backup failed")
        self.log.info("Finished restoring backup after merging")

    def test_backup_merge_with_unmerged(self):
        """
        1. Creates specified bucket on the cluster and loads it with given number of items
        2. Takes two backups - merges them into one
        3. Takes 2 more backups - merges the new backups with already merged ones and validates
        """
        gen = BlobGenerator("ent-backup", "ent-backup-", self.value_size, end=self.num_items)
        self._load_all_buckets(self.master, gen, "create", 0)
        self.backup_create()
        self._take_n_backups(n=2)
        self.backupset.start = 1
        self.backupset.end = 2
        self.log.info("Merging existing incremental backups")
        status, output, message = self.backup_merge()
        if not status:
            self.fail(message)
        self.log.info("Taking more backups")
        self._take_n_backups(n=2)
        self.backupset.start = 1
        self.backupset.end = 3
        self.log.info("Merging new backups into already merged backup")
        status, output, message = self.backup_merge()
        if not status:
            self.fail(message)
        self.log.info("Successfully merged new backups with already merged backup")

    def test_backup_purge(self):
        """
        1. Creates specified bucket on the cluster and loads it with given number of items
        2. Creates a backupset
        3. Initiates a backup and kills the erlang server while backup is going on
        4. Waits for the backup command to timeout
        5. Executes backup command again with purge option
        6. Validates the old backup is deleted and new backup is created successfully
        """
        gen = BlobGenerator("ent-backup", "ent-backup-", self.value_size, end=self.num_items)
        self._load_all_buckets(self.master, gen, "create", 0)
        self.backup_create()
        old_backup_name = ""
        new_backup_name = ""
        backup_result = self.cluster.async_backup_cluster(cluster_host=self.backupset.cluster_host,
                                          backup_host=self.backupset.backup_host,
                                          directory=self.backupset.directory, name=self.backupset.name,
                                          resume=self.backupset.resume, purge=self.backupset.purge,
                                          no_progress_bar=self.no_progress_bar,
                                          cli_command_location=self.cli_command_location)
        self.sleep(10)
        conn = RemoteMachineShellConnection(self.backupset.cluster_host)
        conn.kill_erlang()
        output = backup_result.result(timeout=200)
        self.log.info(str(output))
        status, output, message = self.backup_list()
        if not status:
            self.fail(message)
        for line in output:
            if re.search("\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}.\d{9}Z", line):
                old_backup_name = re.search("\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}.\d{9}Z", line).group()
                self.log.info("Backup name before purge: " + old_backup_name)
        conn.start_couchbase()
        self.sleep(30)
        output, error = self.backup_cluster()
        if error or "Backup successfully completed" not in output[0]:
            self.fail("Taking cluster backup failed.")
        status, output, message = self.backup_list()
        if not status:
            self.fail(message)
        for line in output:
            if re.search("\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}.\d{9}Z", line):
                new_backup_name = re.search("\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}.\d{9}Z", line).group()
                self.log.info("Backup name after purge: " + new_backup_name)
        self.assertNotEqual(old_backup_name, new_backup_name,
                            "Old backup name and new backup name are same when purge is used")
        self.log.info("Old backup name and new backup name are not same when purge is used")

    def test_backup_resume(self):
        """
        1. Creates specified bucket on the cluster and loads it with given number of items
        2. Creates a backupset
        3. Initiates a backup and kills the erlang server while backup is going on
        4. Waits for the backup command to timeout
        5. Executes backup command again with resume option
        6. Validates the old backup is resumes and backup is completed successfully
        """
        gen = BlobGenerator("ent-backup", "ent-backup-", self.value_size, end=self.num_items)
        self._load_all_buckets(self.master, gen, "create", 0)
        self.backup_create()
        old_backup_name = ""
        new_backup_name = ""
        backup_result = self.cluster.async_backup_cluster(cluster_host=self.backupset.cluster_host,
                                          backup_host=self.backupset.backup_host,
                                          directory=self.backupset.directory, name=self.backupset.name,
                                          resume=self.backupset.resume, purge=self.backupset.purge,
                                          no_progress_bar=self.no_progress_bar,
                                          cli_command_location=self.cli_command_location)
        self.sleep(10)
        conn = RemoteMachineShellConnection(self.backupset.cluster_host)
        conn.kill_erlang()
        output = backup_result.result(timeout=200)
        self.log.info(str(output))
        status, output, message = self.backup_list()
        if not status:
            self.fail(message)
        for line in output:
            if re.search("\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}.\d{9}Z", line):
                old_backup_name = re.search("\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}.\d{9}Z", line).group()
                self.log.info("Backup name before resume: " + old_backup_name)
        conn.start_couchbase()
        self.sleep(30)
        output, error = self.backup_cluster()
        if error or "Backup successfully completed" not in output[0]:
            self.fail("Taking cluster backup failed.")
        status, output, message = self.backup_list()
        if not status:
            self.fail(message)
        for line in output:
            if re.search("\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}.\d{9}Z", line):
                new_backup_name = re.search("\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}.\d{9}Z", line).group()
                self.log.info("Backup name after resume: " + new_backup_name)
        self.assertEqual(old_backup_name, new_backup_name,
                         "Old backup name and new backup name are not same when resume is used")
        self.log.info("Old backup name and new backup name are same when resume is used")

    def test_backup_restore_with_deletes(self):
        """
        1. Creates specified bucket on the cluster and loads it with given number of items
        2. Creates a backupset - backsup data and validates
        3. Perform deletes
        4. Restore data and validate
        """
        gen = BlobGenerator("ent-backup", "ent-backup-", self.value_size, end=self.num_items)
        self._load_all_buckets(self.master, gen, "create", 0)
        self.backup_create()
        self.backup_cluster_validate()
        self._load_all_buckets(self.master, gen, "delete", 0)
        self.backup_restore_validate(compare_uuid=False, seqno_compare_function=">=")

    def test_backup_restore_with_failover(self):
        """
        1. Test should be run with 2 nodes in cluster host (param: nodes_init = 2)
        2. Creates specified bucket on the cluster and loads it with given number of items
        3. Creates a backupset - backsup data and validates
        4. Fails over the second node with specified type (param: graceful = True | False)
        5. Sets recovery type to specified value (param: recoveryType = full | delta)
        6. Adds back the failed over node and rebalances
        7. Restores data and validates
        """
        gen = BlobGenerator("ent-backup", "ent-backup-", self.value_size, end=self.num_items)
        self._load_all_buckets(self.master, gen, "create", 0)
        self.backup_create()
        self.backup_cluster_validate()
        rest = RestConnection(self.backupset.cluster_host)
        nodes_all = rest.node_statuses()
        for node in nodes_all:
            if node.ip == self.servers[1].ip:
                rest.fail_over(otpNode=node.id, graceful=self.graceful)
                rest.set_recovery_type(otpNode=node.id, recoveryType=self.recoveryType)
                rest.add_back_node(otpNode=node.id)
        rebalance = self.cluster.async_rebalance(self.servers, [], [])
        rebalance.result()
        self.backup_restore_validate(compare_uuid=False, seqno_compare_function=">=")

    def test_backup_restore_after_upgrade(self):
        """
        1. Test has to be supplied initial_version to be installed and upgrade_version to be upgraded to
        2. Installs initial_version on the servers - initializes them and creates bucket default
        3. Upgrades cluster to upgrade_version
        4. Creates a backupset - backsup data and validates
        5. Restores data and validates
        """
        self._install(self.servers)
        for server in self.servers:
            rest_conn = RestConnection(server)
            rest_conn.init_cluster(username='Administrator', password='asdasd')
            rest_conn.create_bucket(bucket='default', ramQuotaMB=512)
        upgrade_version = self.input.param("upgrade_version", "4.5.0-1069")
        upgrade_threads = self._async_update(upgrade_version=upgrade_version, servers=self.servers)
        for th in upgrade_threads:
            th.join()
        self.log.info("Upgraded to: {ver}".format(ver="4.5.0-1069"))
        gen = BlobGenerator("ent-backup", "ent-backup-", self.value_size, end=self.num_items)
        self._load_all_buckets(self.master, gen, "create", 0)
        self.backup_create()
        self.backup_cluster_validate()
        self.backup_restore_validate(compare_uuid=False, seqno_compare_function=">=")

    def test_backup_restore_with_python_sdk(self):
        """
        1. Note that python sdk has to be installed on all nodes before running this test
        2. Connects to default bucket on cluster host using Python SDK - loads specifed number of items
        3. Creates a backupset, backsup data and validates
        4. Restores data and validates
        5. Connects to default bucket on restore host using Python SDK
        6. Retrieves cas and flgas of each doc on both cluster and restore host - validates if they are equal
        """
        try:
            cb = Bucket('couchbase://' + self.backupset.cluster_host.ip + '/default')
            if cb is not None:
                self.log.info("Established connection to bucket on cluster host using python SDK")
            else:
                self.fail("Failed to establish connection to bucket on cluster host using python SDK")
        except Exception, ex:
            self.fail(str(ex))
        self.log.info("Loading bucket with data using python SDK")
        for i in range(1, self.num_items + 1):
            cb.upsert("doc" + str(i), "value" + str(i))
        cluster_host_data = {}
        for i in range(1, self.num_items + 1):
            key = "doc" + str(i)
            value_obj = cb.get(key=key)
            cluster_host_data[key] = {}
            cluster_host_data[key]["cas"] = str(value_obj.cas)
            cluster_host_data[key]["flags"] = str(value_obj.flags)
        self.backup_create()
        self.backup_cluster_validate()
        self.backup_restore_validate(compare_uuid=False, seqno_compare_function=">=")
        try:
            cb = Bucket('couchbase://' + self.backupset.restore_cluster_host.ip + '/default')
            if cb is not None:
                self.log.info("Established connection to bucket on restore host using python SDK")
            else:
                self.fail("Failed to establish connection to bucket on restore host using python SDK")
        except Exception, ex:
            self.fail(str(ex))
        restore_host_data = {}
        for i in range(1, self.num_items + 1):
            key = "doc" + str(i)
            value_obj = cb.get(key=key)
            restore_host_data[key] = {}
            restore_host_data[key]["cas"] = str(value_obj.cas)
            restore_host_data[key]["flags"] = str(value_obj.flags)
        self.log.info("Comparing cluster host data cas and flags against restore host data")
        for i in range(1, self.num_items + 1):
            key = "doc" + str(i)
            if cluster_host_data[key]["cas"] != restore_host_data[key]["cas"]:
                self.fail("CAS mismatch for key: {0}".format(key))
            if cluster_host_data[key]["flags"] != restore_host_data[key]["flags"]:
                self.fail("Flags mismatch for key: {0}".format(key))
        self.log.info("Successfully validated cluster host data cas and flags against restore host data")

    def test_backup_restore_with_flush(self):
        """
        1. Test should be run with same-cluster=True
        2. Creates specified bucket on the cluster and loads it with given number of items
        3. Creates a backupset - backsup data and validates
        4. Flushes the bucket
        5. Restores data and validates
        """
        gen = BlobGenerator("ent-backup", "ent-backup-", self.value_size, end=self.num_items)
        self._load_all_buckets(self.master, gen, "create", 0)
        self.backup_create()
        self.backup_cluster_validate()
        rest = RestConnection(self.backupset.cluster_host)
        rest.flush_bucket()
        self.log.info("Flushed default bucket - restoring data now..")
        self.backup_restore_validate(compare_uuid=False, seqno_compare_function=">=")

    def test_backup_restore_with_recreate(self):
        """
        1. Test should be run with same-cluster=True
        2. Creates specified bucket on the cluster and loads it with given number of items
        3. Creates a backupset - backsup data and validates
        4. Deletes the bucket and recreates it
        5. Restores data and validates
        """
        gen = BlobGenerator("ent-backup", "ent-backup-", self.value_size, end=self.num_items)
        self._load_all_buckets(self.master, gen, "create", 0)
        self.backup_create()
        self.backup_cluster_validate()
        rest = RestConnection(self.backupset.cluster_host)
        rest.delete_bucket()
        rest.create_bucket(bucket="default",ramQuotaMB=512)
        self.log.info("Deleted default bucket and recreated it - restoring it now..")
        self.backup_restore_validate(compare_uuid=False, seqno_compare_function=">=")

    def test_backup_create_negative_args(self):
        """
        Validates error messages for negative inputs of create command
        """
        remote_client = RemoteMachineShellConnection(self.backupset.backup_host)
        cmd = "create"
        command = "{0}/backup {1}".format(self.cli_command_location, cmd)
        output, error = remote_client.execute_command(command)
        remote_client.log_command_output(output, error)
        self.assertEqual(output[0], "Error: Required flag --dir not specified", "Expected error message not thrown")
        cmd = "create --dir"
        command = "{0}/backup {1}".format(self.cli_command_location, cmd)
        output, error = remote_client.execute_command(command)
        remote_client.log_command_output(output, error)
        self.assertEqual(error[0], "flag needs an argument: -dir", "Expected error message not thrown")
        cmd = "create --dir {0}".format(self.backupset.directory)
        command = "{0}/backup {1}".format(self.cli_command_location, cmd)
        output, error = remote_client.execute_command(command)
        remote_client.log_command_output(output, error)
        self.assertEqual(output[0], "Error: Required flag --name not specified", "Expected error message not thrown")
        cmd = "create --dir {0} --name".format(self.backupset.directory)
        command = "{0}/backup {1}".format(self.cli_command_location, cmd)
        output, error = remote_client.execute_command(command)
        remote_client.log_command_output(output, error)
        self.assertEqual(error[0], "flag needs an argument: -name", "Expected error message not thrown")
        self.backup_create()
        cmd = "create --dir {0} --name {1}".format(self.backupset.directory, self.backupset.name)
        command = "{0}/backup {1}".format(self.cli_command_location, cmd)
        output, error = remote_client.execute_command(command)
        remote_client.log_command_output(output, error)
        self.assertEqual(output[0], "Backup creation failed: Backup Set `backup` exists",
                                    "Expected error message not thrown")

    def test_backup_cluster_restore_negative_args(self):
        """
        Validates error messages for negative inputs of cluster or restore command - command parameter
        decides which command to test
        """
        remote_client = RemoteMachineShellConnection(self.backupset.backup_host)
        self.backup_create()
        cmd_to_test = self.input.param("command", "cluster")
        if cmd_to_test == "restore":
            cmd = cmd_to_test + " --dir {0} --name {1} --host http://{2}:{3} --username {4} \
                                  --password {5}".format(self.backupset.directory,
                                                         self.backupset.name,
                                                         self.backupset.cluster_host.ip,
                                                         self.backupset.cluster_host.port,
                                                         self.backupset.cluster_host_username,
                                                         self.backupset.cluster_host_password)
            command = "{0}/backup {1}".format(self.cli_command_location, cmd)
            output, error = remote_client.execute_command(command)
            remote_client.log_command_output(output, error)
            self.assertTrue("Error restoring cluster: Backup backup doesn't contain any backups" in output[-1],
                            "Expected error message not thrown")
            self.backup_cluster()
        cmd = cmd_to_test
        command = "{0}/backup {1}".format(self.cli_command_location, cmd)
        output, error = remote_client.execute_command(command)
        remote_client.log_command_output(output, error)
        self.assertEqual(output[0], "Error: Required flag --dir not specified", "Expected error message not thrown")
        cmd = cmd_to_test + " --dir"
        command = "{0}/backup {1}".format(self.cli_command_location, cmd)
        output, error = remote_client.execute_command(command)
        remote_client.log_command_output(output, error)
        self.assertEqual(error[-1], "flag needs an argument: -dir", "Expected error message not thrown")
        cmd = cmd_to_test + " --dir abc"
        command = "{0}/backup {1}".format(self.cli_command_location, cmd)
        output, error = remote_client.execute_command(command)
        remote_client.log_command_output(output, error)
        self.assertEqual(output[0], "Error: Archive directory `abc` doesn't exist", "Expected error message not thrown")
        cmd = cmd_to_test + " --dir {0}".format(self.backupset.directory)
        command = "{0}/backup {1}".format(self.cli_command_location, cmd)
        output, error = remote_client.execute_command(command)
        remote_client.log_command_output(output, error)
        self.assertEqual(output[0], "Error: Required flag --name not specified", "Expected error message not thrown")
        cmd = cmd_to_test + " --dir {0} --name".format(self.backupset.directory)
        command = "{0}/backup {1}".format(self.cli_command_location, cmd)
        output, error = remote_client.execute_command(command)
        remote_client.log_command_output(output, error)
        self.assertEqual(error[-1], "flag needs an argument: -name", "Expected error message not thrown")
        cmd = cmd_to_test + " --dir {0} --name {1}".format(self.backupset.directory, self.backupset.name)
        command = "{0}/backup {1}".format(self.cli_command_location, cmd)
        output, error = remote_client.execute_command(command)
        remote_client.log_command_output(output, error)
        self.assertEqual(output[0], "Error: Required flag --host not specified", "Expected error message not thrown")
        cmd = cmd_to_test + " --dir {0} --name {1} --host".format(self.backupset.directory, self.backupset.name)
        command = "{0}/backup {1}".format(self.cli_command_location, cmd)
        output, error = remote_client.execute_command(command)
        remote_client.log_command_output(output, error)
        self.assertEqual(error[-1], "flag needs an argument: -host", "Expected error message not thrown")
        cmd = cmd_to_test + " --dir {0} --name {1} --host http://{2}:{3}".format(self.backupset.directory,
                                                                                 self.backupset.name,
                                                                                 self.backupset.cluster_host.ip,
                                                                                 self.backupset.cluster_host.port)
        command = "{0}/backup {1}".format(self.cli_command_location, cmd)
        output, error = remote_client.execute_command(command)
        remote_client.log_command_output(output, error)
        self.assertEqual(output[0], "Error: Required flag --username not specified", "Expected error message not thrown")
        cmd = cmd_to_test + " --dir {0} --name {1} --host http://{2}:{3} \
                              --username".format(self.backupset.directory,
                                                 self.backupset.name,
                                                 self.backupset.cluster_host.ip,
                                                 self.backupset.cluster_host.port)
        command = "{0}/backup {1}".format(self.cli_command_location, cmd)
        output, error = remote_client.execute_command(command)
        remote_client.log_command_output(output, error)
        self.assertEqual(error[-1], "flag needs an argument: -username", "Expected error message not thrown")
        cmd = cmd_to_test + " --dir {0} --name {1} --host http://{2}:{3} \
                              --username {4}".format(self.backupset.directory,
                                                     self.backupset.name,
                                                     self.backupset.cluster_host.ip,
                                                     self.backupset.cluster_host.port,
                                                     self.backupset.cluster_host_username)
        command = "{0}/backup {1}".format(self.cli_command_location, cmd)
        output, error = remote_client.execute_command(command)
        remote_client.log_command_output(output, error)
        self.assertEqual(output[0], "Error: Required flag --password not specified", "Expected error message not thrown")
        cmd = cmd_to_test + " --dir {0} --name {1} --host http://{2}:{3} --username {4} \
                              --password".format(self.backupset.directory,
                                                 self.backupset.name,
                                                 self.backupset.cluster_host.ip,
                                                 self.backupset.cluster_host.port,
                                                 self.backupset.cluster_host_username)
        command = "{0}/backup {1}".format(self.cli_command_location, cmd)
        output, error = remote_client.execute_command(command)
        remote_client.log_command_output(output, error)
        self.assertEqual(error[-1], "flag needs an argument: -password", "Expected error message not thrown")
        cmd = cmd_to_test + " --dir {0} --name abc --host http://{1}:{2} --username {3} \
                              --password {4}".format(self.backupset.directory,
                                                     self.backupset.cluster_host.ip,
                                                     self.backupset.cluster_host.port,
                                                     self.backupset.cluster_host_username,
                                                     self.backupset.cluster_host_password)
        command = "{0}/backup {1}".format(self.cli_command_location, cmd)
        output, error = remote_client.execute_command(command)
        remote_client.log_command_output(output, error)
        self.assertTrue("Backup Set `abc` not found" in output[-1], "Expected error message not thrown")
        cmd = cmd_to_test + " --dir {0} --name {1} --host abc --username {2} \
                              --password {3}".format(self.backupset.directory,
                                                     self.backupset.name,
                                                     self.backupset.cluster_host_username,
                                                     self.backupset.cluster_host_password)
        command = "{0}/backup {1}".format(self.cli_command_location, cmd)
        output, error = remote_client.execute_command(command)
        remote_client.log_command_output(output, error)
        self.assertTrue("no such host" in output[-1], "Expected error message not thrown")
        cmd = cmd_to_test + " --dir {0} --name {1} --host http://{2}:{3} --username abc \
                              --password {4}".format(self.backupset.directory,
                                                     self.backupset.name,
                                                     self.backupset.cluster_host.ip,
                                                     self.backupset.cluster_host.port,
                                                     self.backupset.cluster_host_password)
        command = "{0}/backup {1}".format(self.cli_command_location, cmd)
        output, error = remote_client.execute_command(command)
        remote_client.log_command_output(output, error)
        self.assertTrue("check username and password" in output[-1], "Expected error message not thrown")
        cmd = cmd_to_test + " --dir {0} --name {1} --host http://{2}:{3} --username {4} \
                              --password abc".format(self.backupset.directory,
                                                     self.backupset.name,
                                                     self.backupset.cluster_host.ip,
                                                     self.backupset.cluster_host.port,
                                                     self.backupset.cluster_host_username)
        command = "{0}/backup {1}".format(self.cli_command_location, cmd)
        output, error = remote_client.execute_command(command)
        remote_client.log_command_output(output, error)
        self.assertTrue("check username and password" in output[-1], "Expected error message not thrown")

    def test_backup_list_negative_args(self):
        """
        Validates error messages for negative inputs of list command
        """
        remote_client = RemoteMachineShellConnection(self.backupset.backup_host)
        self.backup_create()
        cmd = "list"
        command = "{0}/backup {1}".format(self.cli_command_location, cmd)
        output, error = remote_client.execute_command(command)
        remote_client.log_command_output(output, error)
        self.assertEqual(output[0], "Error: Required flag --dir not specified", "Expected error message not thrown")
        cmd = "list --dir"
        command = "{0}/backup {1}".format(self.cli_command_location, cmd)
        output, error = remote_client.execute_command(command)
        remote_client.log_command_output(output, error)
        self.assertEqual(error[0], "flag needs an argument: -dir", "Expected error message not thrown")
        cmd = "list --dir abc".format(self.backupset.directory)
        command = "{0}/backup {1}".format(self.cli_command_location, cmd)
        output, error = remote_client.execute_command(command)
        remote_client.log_command_output(output, error)
        self.assertTrue("Error: Archive directory `abc` doesn't exist" in output[-1],
                        "Expected error message not thrown")