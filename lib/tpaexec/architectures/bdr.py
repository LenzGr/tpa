#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# © Copyright EnterpriseDB UK Limited 2015-2022 - All rights reserved.

from ..architecture import Architecture
from ..exceptions import BDRArchitectureError
from typing import List, Tuple


class BDR(Architecture):
    def supported_versions(self) -> List[Tuple[str, str]]:
        """
        Returns a list of (postgres_version, bdr_version) tuples that this
        architecture supports. Meant to be implemented by subclasses.
        """
        return []

    def bdr_major_versions(self) -> List[str]:
        """
        Returns a list of BDR major versions supported by this architecture.
        """
        return list(set(map(lambda t: t[1], self.supported_versions())))

    def add_architecture_options(self, p, g):
        g.add_argument(
            "--bdr-version",
            metavar="VER",
            help="major version of BDR required",
            dest="bdr_version",
            choices=self.bdr_major_versions(),
        )
        g.add_argument(
            "--bdr-node-group",
            metavar="NAME",
            help="name of BDR node group",
            default="bdrgroup",
        )
        g.add_argument(
            "--bdr-database",
            metavar="NAME",
            help="name of BDR-enabled database",
            default="bdrdb",
        )
        g.add_argument(
            "--enable-camo",
            action="store_true",
            help="assign instances pairwise as CAMO partners",
        )

    def cluster_vars_args(self):
        return super().cluster_vars_args() + [
            "bdr_version",
            "bdr_node_group",
            "bdr_database",
            "harp_consensus_protocol",
        ]

    def update_cluster_vars(self, cluster_vars):
        # We must decide which version of Postgres to install, which version
        # of BDR to install, and which repositories and extensions must be
        # enabled for the combination to work.
        #
        # If --postgres-version is specified, we infer the correct BDR
        # version. If --bdr-version is specified, we infer the correct
        # Postgres version. If both are specified, we check that the
        # combination makes sense.
        #
        # If any --2Q-repositories are specified, we do not interfere with
        # that setting at all.

        tpa_2q_repositories = self.args.get("tpa_2q_repositories") or []
        postgresql_flavour = self.args.get("postgresql_flavour") or "postgresql"
        postgres_version = self.args.get("postgres_version")
        bdr_version = self.args.get("bdr_version")
        harp_enabled = self.args.get("failover_manager") == "harp"

        given_repositories = " ".join(tpa_2q_repositories)

        default_bdr_versions = {
            "9.4": "1",
            "9.6": "2",
            "10": "3",
            "11": "3",
            "12": "3",
            "13": "3",
            "14": "4",
            None: "3",
        }

        default_pg_versions = {
            "1": "9.4",
            "2": "9.6",
            "3": "13",
            "4": "14",
        }

        if bdr_version is None:
            bdr_version = default_bdr_versions.get(postgres_version)
        if postgres_version is None:
            postgres_version = default_pg_versions.get(bdr_version)

        if (postgres_version, bdr_version) not in self.supported_versions():
            raise BDRArchitectureError(
                f"Postgres {postgres_version} with BDR {bdr_version} is not supported"
            )

        extensions = []

        if bdr_version == "1":
            postgresql_flavour = "postgresql-bdr"
        elif bdr_version == "2":
            if not tpa_2q_repositories or "/bdr2/" not in given_repositories:
                tpa_2q_repositories.append("products/bdr2/release")
        elif bdr_version == "3":
            extensions = ["pglogical"]
            if not tpa_2q_repositories:
                if postgresql_flavour == "pgextended":
                    tpa_2q_repositories.append("products/bdr_enterprise_3_7/release")
                elif postgresql_flavour == "epas":
                    tpa_2q_repositories.append(
                        "products/bdr_enterprise_3_7-epas/release"
                    )
                else:
                    tpa_2q_repositories.append("products/bdr3_7/release")
                    tpa_2q_repositories.append("products/pglogical3_7/release")
        elif bdr_version == "4":
            if not tpa_2q_repositories or "/bdr4/" not in given_repositories:
                tpa_2q_repositories.append("products/bdr4/release")
            if postgresql_flavour == "pgextended" and (
                not tpa_2q_repositories or "/2ndqpostgres/" not in given_repositories
            ):
                tpa_2q_repositories.append("products/2ndqpostgres/release")

        elif bdr_version == "5":
            if not tpa_2q_repositories or "/bdr5/" not in given_repositories:
                tpa_2q_repositories.append("products/bdr5/release")
            if postgresql_flavour == "pgextended" and (
                not tpa_2q_repositories or "/2ndqpostgres/" not in given_repositories
            ):
                tpa_2q_repositories.append("products/2ndqpostgres/release")

        if harp_enabled and (
            not tpa_2q_repositories or "/harp/" not in given_repositories
        ):
            tpa_2q_repositories.append("products/harp/release")

        cluster_vars.update(
            {
                "postgres_coredump_filter": "0xff",
                "bdr_version": bdr_version,
                "postgres_version": postgres_version,
                "postgresql_flavour": postgresql_flavour,
            }
        )

        if tpa_2q_repositories:
            cluster_vars.update(
                {
                    "tpa_2q_repositories": tpa_2q_repositories,
                }
            )

        if extensions:
            cluster_vars.update({"extra_postgres_extensions": extensions})

    def update_instances(self, instances):

        self._update_instance_camo(instances)
        self._update_instance_pem(instances)

    @property
    def _candidate_roles(self):
        """
        Instance roles that can be considered as BDR node candidates.

        Returns: Set of roles names

        """
        return {
            "bdr",
            "replica",
            "readonly",
            "subscriber-only",
            "witness",
        }

    @property
    def _camo_candidate_roles(self):
        """
        Instance roles that can be considered a CAMO partner.

        Returns: Set of role names

        """
        return self._candidate_roles

    @property
    def _pemagent_candidate_roles(self):
        """
        Instance roles that should run PEM agent.

        Returns: Set of role names

        """
        return self._candidate_roles.union({"primary"})

    def _instance_roles(self, instance):
        """Get the roles for an instance."""
        ins_defs = self.args["instance_defaults"]
        return set(instance.get("role", ins_defs.get("role", [])))

    def _update_instance_camo(self, instances):
        """
        If --enable-camo is specified, we collect all the instances with role
        [bdr,primary] and no partner already set and set them pairwise to be
        each other's CAMO partners. This is crude, but it's good enough to
        experiment with CAMO.
        """
        if self.args.get("enable_camo", False):
            bdr_primaries = []
            for instance in instances:
                _vars = instance.get("vars", {})
                if (
                    self._camo_candidate_roles & self._instance_roles(instance)
                    and "bdr_node_camo_partner" not in _vars
                ):
                    bdr_primaries.append(instance)

            idx = 0
            while idx + 1 < len(bdr_primaries):
                a = bdr_primaries[idx]
                b = bdr_primaries[idx + 1]

                a_vars = a.get("vars", {})
                a_vars["bdr_node_camo_partner"] = b.get("Name")
                a["vars"] = a_vars

                b_vars = b.get("vars", {})
                b_vars["bdr_node_camo_partner"] = a.get("Name")
                b["vars"] = b_vars

                idx += 2

    def _update_instance_pem(self, instances):
        """
        Add pem-agent to instance roles where applicable.

        If --enable-pem is specified, we collect all the instances with role
        [primary, bdr, replica, readonly, witness, subscriber-only] and append
        'pem-agent' role to the existing set of roles assigned to them. We later
        add a dedicated 'pemserver' instance to host our PEM server.
        """
        if self.args.get("enable_pem", False):

            for instance in instances:
                if self._pemagent_candidate_roles & self._instance_roles(instance):
                    instance["role"].append("pem-agent")

                if "barman" in self._instance_roles(instance) and self.args.get(
                    "enable_pg_backup_api", False
                ):
                    instance["role"].append("pem-agent")

            n = instances[-1].get("node")
            instances.append(
                {
                    "node": n + 1,
                    "Name": "pemserver",
                    "role": ["pem-server"],
                    "location": self.args["locations"][0]["Name"],
                }
            )
