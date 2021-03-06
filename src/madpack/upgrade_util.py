import re
import sys
import yaml
from collections import defaultdict
import os

"""
@brief Wrapper function for ____run_sql_query
"""
def run_sql(sql, portid, con_args):
    from madpack import ____run_sql_query
    return ____run_sql_query(sql, True, portid, con_args)

"""
@brief Get the signature of a UDF/UDA for comparison
"""
def get_signature_for_compare(schema, proname, rettype, argument):
    signature = '%s %s.%s(%s)' % (
        rettype.strip(), schema.strip(), proname.strip(), argument.strip())
    signature = re.sub('\s+', ' ', signature)
    signature = re.sub('"', '', signature)
    return signature.lower()

"""
@brief Base class for handling the upgrade
"""
class UpgradeBase:
    def __init__(self, schema, portid, con_args):
        self._schema = schema.lower()
        self._portid = portid
        self._con_args = con_args
        self._schema_oid = None
        self._get_schema_oid()

    """
    @brief Wrapper function for run_sql
    """
    def _run_sql(self, sql):
        return run_sql(sql, self._portid, self._con_args)

    """
    @brief Get the oids of some objects from the catalog in the current version
    """
    def _get_schema_oid(self):
        self._schema_oid = self._run_sql("""
            SELECT oid FROM pg_namespace WHERE nspname = '{schema}'
            """.format(schema=self._schema))[0]['oid']


    """
    @brief Get the function name, return type, and arguments given an oid
    @note The function can only handle the case that proallargtypes is null,
    refer to pg_catalog.pg_get_function_identity_argument and
    pg_catalog.pg_get_function_result in PG for a complete implementation, which are
    not supported by GP
    """
    def _get_function_info(self, oid):
        row = self._run_sql("""
            SELECT
                max(proname) AS proname,
                max(rettype) AS rettype,
                array_to_string(
                    array_agg(argname || ' ' || argtype order by i), ', ') AS argument
            FROM
            (
                SELECT
                    proname,
                    textin(regtypeout(prorettype::regtype)) AS rettype,
                    CASE array_upper(proargtypes,1) WHEN -1 THEN ''
                        ELSE textin(regtypeout(unnest(proargtypes)::regtype))
                    END AS argtype,
                    CASE WHEN proargnames IS NULL THEN ''
                        ELSE unnest(proargnames)
                    END AS argname,
                    CASE array_upper(proargtypes,1) WHEN -1 THEN 1
                        ELSE generate_series(0, array_upper(proargtypes, 1))
                    END AS i
                FROM
                    pg_proc AS p
                WHERE
                    oid = {oid}
            ) AS f
            """.format(oid=oid))
        return {"proname": row[0]['proname'], 'rettype': row[0]['rettype'],
            'argument': row[0]['argument']}

"""
@brief This class reads changes from the configuration file and handles
the dropping of objects
"""
class ChangeHandler(UpgradeBase):
    def __init__(self, schema, portid, con_args, maddir, mad_dbrev):
        UpgradeBase.__init__(self, schema, portid, con_args)
        self._opr_ind_svec = None
        self._get_opr_indepent_svec()
        self._maddir = maddir
        self._mad_dbrev = mad_dbrev
        self._newmodule = None
        self._udt = None
        self._udf = None
        self._uda = None
        self._udc = None
        self._load()


    """
    @brief Get the UDOps which are independent of svec in the current version
    """
    def _get_opr_indepent_svec(self):
        rows = self._run_sql("""
            SELECT
                oprname,
                tl.typname AS typ_left,
                nspl.nspname AS nsp_left,
                tr.typname AS typ_right,
                nspr.nspname AS nsp_right
            FROM
                pg_operator AS o,
                pg_type AS tl,
                pg_type AS tr,
                pg_namespace AS nspl,
                pg_namespace AS nspr
            WHERE
                oprnamespace = {schema_oid} AND
                (
                    oprleft <> '{schema_madlib}.svec'::regtype AND
                    oprright <> '{schema_madlib}.svec'::regtype AND
                    oprleft = tl.oid AND
                    oprright = tr.oid AND
                    tl.typnamespace = nspl.oid AND
                    tr.typnamespace = nspr.oid
                )
            """.format(schema_madlib=self._schema, schema_oid=self._schema_oid))
        self._opr_ind_svec = {}
        for row in rows:
            self._opr_ind_svec[row['oprname']] = row

    def _load_config_param(self, config_iterable):
        """
        Replace schema_madlib with the appropriate schema name and
        make all function names lower case to ensure ease of comparison.

        Args:
            @param config_dict is a dictionary with key as object name
                        (eg. function name) and value as the details for
                        the object. The details for the object are assumed to
                        be in a dictionary with following keys:
                            rettype: Return type
                            argument: List of arguments

        Returns:
            A dictionary that lists all specific objects (functions, aggregates, etc)
            with object name as key and another dictionary with objects details
            as the value.
        """
        _return_obj = defaultdict(list)
        if config_iterable is not None:
            for each_config in config_iterable:
                for obj_name, obj_details in each_config.iteritems():
                    rettype = obj_details['rettype'].lower().replace(
                                                'schema_madlib', self._schema)
                    if obj_details['argument'] is not None:
                        argument = obj_details['argument'].lower().replace(
                                                'schema_madlib', self._schema)
                    _return_obj[obj_name].append(
                                    {'rettype': rettype, 'argument': argument})
        return _return_obj

    """
    @brief Load the configuration file
    """
    def _load(self):
        # _mad_dbrev = 1.0
        if float(self._mad_dbrev) < 1.1:
            filename = os.path.join(self._maddir, 'madpack' , 'changelist_1.0_1.2.yaml')
        # _mad_dbrev = 1.1
        else:
            filename = os.path.join(self._maddir, 'madpack' , 'changelist.yaml')
            
        config = yaml.load(open(filename))

        if config['new module'] is not None:
            self._newmodule = config['new module']
        else:
            self._newmodule = {}

        if config['udt'] is not None:
            self._udt = config['udt']
        else:
            self._udt = {}

        if config['udc'] is not None:
            self._udc = config['udc']
        else:
            self._udc = {}

        self._udf = self._load_config_param(config['udf'])
        self._uda = self._load_config_param(config['uda'])

    """
    @brief Get the list of new modules
    """
    def get_newmodule(self):
        return self._newmodule

    """
    @brief Get the list of changed UDTs
    """
    def get_udt(self):
        return self._udt

    """
    @brief Get the list of changed UDAs
    """
    def get_uda(self):
        return self._uda

    """
    @brief Get the list of changed UDCs
    @note This is a UDC in utilities module
    """
    def get_udc(self):
        return self._udc

    """
    @brief Get the list of UDF signatures for comparison
    """
    def get_udf_signature(self):
        res = defaultdict(bool)
        for udf in self._udf:
            for item in self._udf[udf]:
                signature = get_signature_for_compare(
                    self._schema, udf, item['rettype'], item['argument'])
                res[signature] = True
        return res

    """
    @brief Get the list of UDA signatures for comparison
    """
    def get_uda_signature(self):
        res = defaultdict(bool)
        for uda in self._uda:
            for item in self._uda[uda]:
                signature = get_signature_for_compare(
                    self._schema, uda, item['rettype'], item['argument'])
                res[signature] = True
        return res

    """
    @brief Drop all types that were updated/removed in the new version
    @note It is dangerous to drop a UDT becuase there might be many
    dependencies
    """
    def drop_changed_udt(self):
        # Note that we use CASCADE option here. This might be dangerous because
        # it may drop some undetected dependent objects (eg. UDCast, UDOp, etc)
        for udt in self._udt:
            self._run_sql("""
                DROP TYPE IF EXISTS {schema}.{udt} CASCADE
                """.format(schema=self._schema, udt=udt))
            if udt == 'svec':
                # Drop operators defined in the svec module which do not
                # depend on svec. We will run the whole svec.sql without
                # filtering once svec changed
                for opr in self._opr_ind_svec:
                    self._run_sql("""
                        DROP OPERATOR IF EXISTS {schema}.{oprname}
                        ({nsp_left}.{typ_left}, {nsp_left}.{typ_left})
                        """.format(
                            schema=self._schema, oprname=opr,
                            nsp_left=self._opr_ind_svec[opr]['nsp_left'],
                            typ_left=self._opr_ind_svec[opr]['typ_left'],
                            nsp_right=self._opr_ind_svec[opr]['nsp_right'],
                            typ_right=self._opr_ind_svec[opr]['typ_right']
                        ))
    """
    @brief Drop all functions (UDF) that were removed in new version
    """
    def drop_changed_udf(self):
        for udf in self._udf:
            for item in self._udf[udf]:
                self._run_sql("""
                    DROP FUNCTION IF EXISTS {schema}.{udf}({arg})
                    """.format(schema=self._schema,
                        udf=udf,
                        arg=item['argument']))

    """
    @brief Drop all aggregates (UDA) that were removed in new version
    """
    def drop_changed_uda(self):
        for uda in self._uda:
            for item in self._uda[uda]:
                self._run_sql("""
                    DROP AGGREGATE IF EXISTS {schema}.{uda}({arg})
                    """.format(schema=self._schema,
                        uda=uda,
                        arg=item['argument']))

    """
    @brief Drop all casts (UDC) that were updated/removed in new version
    @note We have special treatment for UDCs defined in the svec module
    """
    def drop_changed_udc(self):
        for udc in self._udc:
            self._run_sql("""
                DROP CAST IF EXISTS ({sourcetype} AS {targettype})
                """.format(
                    sourcetype=self._udc[udc]['sourcetype'],
                    targettype=self._udc[udc]['targettype']))

"""
@brief This class detects the direct/recursive view dependencies on MADLib
UDFs/UDAs defined in the current version
"""
class ViewDependency(UpgradeBase):
    def __init__(self, schema, portid, con_args):
        UpgradeBase.__init__(self, schema, portid, con_args)
        self._view2proc = None
        self._view2view = None
        self._view2def = None
        self._detect_direct_view_dependency()
        self._detect_recursive_view_dependency()
        self._filter_recursive_view_dependency()

    """
    @brief  Detect direct view dependencies on MADLib UDFs/UDAs
    """
    def _detect_direct_view_dependency(self):
        rows = self._run_sql("""
            SELECT
                view, nsp.nspname AS schema, procname, procoid, proisagg
            FROM
                pg_namespace nsp,
                (
                    SELECT
                        c.relname AS view,
                        c.relnamespace AS namespace,
                        p.proname As procname,
                        p.oid AS procoid,
                        p.proisagg AS proisagg
                    FROM
                        pg_class AS c,
                        pg_rewrite AS rw,
                        pg_depend AS d,
                        pg_proc AS p
                    WHERE
                        c.oid = rw.ev_class AND
                        rw.oid = d.objid AND
                        d.classid = 'pg_rewrite'::regclass AND
                        d.refclassid = 'pg_proc'::regclass AND
                        d.refobjid = p.oid AND
                        p.pronamespace = {schema_madlib_oid}
                ) t1
            WHERE
                t1.namespace = nsp.oid
        """.format(schema_madlib_oid=self._schema_oid))

        self._view2proc = defaultdict(list)
        for row in rows:
            key= (row['schema'], row['view'])
            self._view2proc[key].append(
                (row['procname'], row['procoid'],
                    True if row['proisagg'] == 't' else False))

    """
    @brief  Detect recursive view dependencies (view on view)
    """
    def _detect_recursive_view_dependency(self):
        rows = self._run_sql("""
            SELECT
                nsp1.nspname AS depender_schema,
                depender,
                nsp2.nspname AS dependee_schema,
                dependee
            FROM
                pg_namespace AS nsp1,
                pg_namespace AS nsp2,
                (
                    SELECT
                        c.relname depender,
                        c.relnamespace AS depender_nsp,
                        c1.relname AS dependee,
                        c1.relnamespace AS dependee_nsp
                    FROM
                        pg_rewrite AS rw,
                        pg_depend AS d,
                        pg_class AS c,
                        pg_class AS c1
                    WHERE
                        rw.ev_class = c.oid AND
                        rw.oid = d.objid AND
                        d.classid = 'pg_rewrite'::regclass AND
                        d.refclassid = 'pg_class'::regclass AND
                        d.refobjid = c1.oid AND
                        c1.relkind = 'v' AND
                        c.relname <> c1.relname
                    GROUP BY
                        depender, depender_nsp, dependee, dependee_nsp
                ) t1
            WHERE
                t1.depender_nsp = nsp1.oid AND
                t1.dependee_nsp = nsp2.oid
        """)

        self._view2view = defaultdict(list)
        for row in rows:
            key = (row['depender_schema'], row['depender'])
            val = (row['dependee_schema'], row['dependee'])
            self._view2view[key].append(val)

    """
    @brief  Filter out recursive view dependencies which are independent of
    MADLib UDFs/UDAs
    """
    def _filter_recursive_view_dependency(self):
        # Get recursive dependee list
        dependeelist = []
        checklist = self._view2proc
        while True:
            dependeelist.extend(checklist.keys())
            new_checklist = defaultdict(bool)
            for depender in self._view2view.keys():
                for dependee in self._view2view[depender]:
                    if dependee in checklist:
                        new_checklist[depender] = True
                        break
            if len(new_checklist) == 0:
                break
            else:
                checklist = new_checklist

        # Filter recursive dependencies not related with MADLib UDF/UDAs
        fil_view2view = defaultdict(list)
        for depender in self._view2view:
            dependee = self._view2view[depender]
            dependee = [r for r in dependee if r in dependeelist]
            if len(dependee) > 0:
                fil_view2view[depender] = dependee

        self._view2view = fil_view2view

    """
    @brief  Build the dependency graph (depender-to-dependee adjacency list)
    """
    def _build_dependency_graph(self, hasProcDependency = False):
        der2dee = self._view2view.copy()
        for view in self._view2proc:
            if view not in self._view2view:
                der2dee[view] = []
            if hasProcDependency:
                der2dee[view].extend(self._view2proc[view])

        graph = der2dee.copy()
        for der in der2dee:
            for dee in der2dee[der]:
                if dee not in graph:
                    graph[dee] = []
        return graph

    """
    @brief Check dependencies
    """
    def has_dependency(self):
        return len(self._view2proc) > 0

    """
    @brief Get the ordered views for creation
    """
    def get_create_order_views(self):
        graph = self._build_dependency_graph()
        ordered_views = []
        while True:
            remove_list = []
            for depender in graph:
                if len(graph[depender]) == 0:
                    ordered_views.append(depender)
                    remove_list.append(depender)
            for view in remove_list:
                del graph[view]
            for depender in graph:
                graph[depender] = [r for r in graph[depender]
                    if r not in remove_list]
            if len(remove_list) == 0:
                break
        return ordered_views

    """
    @brief Get the ordered views for dropping
    """
    def get_drop_order_views(self):
        ordered_views = self.get_create_order_views()
        ordered_views.reverse()
        return ordered_views

    """
    @brief Get the depended UDF/UDA signatures for comparison
    """
    def get_depended_func_signature(self, isagg = True):
        res = {}
        for procs in self._view2proc.values():
            for proc in procs:
                if proc[2] != isagg:
                    continue
                if (self._schema, proc) not in res:
                    funcinfo = self._get_function_info(proc[1])
                    signature = get_signature_for_compare(
                        self._schema, proc[0], funcinfo['rettype'], funcinfo['argument'])
                    res[signature] = True
        return res

    """
    @brief Get dependent UDAs
    """
    def get_depended_uda(self):
        res = []
        for procs in self._view2proc.values():
            for proc in procs:
                if proc[2] == False:
                    # proc is not an aggregate -> skip
                    continue
                if (self._schema, proc) not in res:
                    res.append((self._schema, proc))
        res.sort()
        return res

    """
    @brief Get dependent UDFs
    """
    def get_depended_udf(self):
        res = []
        for procs in self._view2proc.values():
            for proc in procs:
                if proc[2] == True:
                    # proc is an aggregate -> skip
                    continue
                if (self._schema, proc) not in res:
                    res.append((self._schema, proc))
        res.sort()
        return res

    """
    @brief Save and drop the dependent views
    """
    def save_and_drop(self):
        self._view2def = {}
        ordered_views = self.get_drop_order_views()
        # Save views
        for view in ordered_views:
            row = self._run_sql("""
                    SELECT
                        schemaname, viewname, viewowner, definition
                    FROM
                        pg_views
                    WHERE
                        schemaname = '{schemaname}' AND
                        viewname = '{viewname}'
                    """.format(schemaname=view[0], viewname=view[1]))
            self._view2def[view] = row[0]

        # Drop views
        for view in ordered_views:
            self._run_sql("""
                DROP VIEW IF EXISTS {schema}.{view}
                """.format(schema=view[0], view=view[1]))

    """
    @brief Restore the dependent views
    """
    def restore(self):
        ordered_views = self.get_create_order_views()
        for view in ordered_views:
            row = self._view2def[view]
            schema = row['schemaname']
            view = row['viewname']
            owner = row['viewowner']
            definition = row['definition']
            self._run_sql("""
                --Alter view not supported by GP, so use set/reset role as a
                --workaround
                --ALTER VIEW {schema}.{view} OWNER TO {owner}
                SET ROLE {owner};
                CREATE OR REPLACE VIEW {schema}.{view} AS {definition};
                RESET ROLE
                """.format(
                    schema=schema, view=view,
                    definition=definition, owner=owner))

    def _node_to_str(self, node):
        res = ''
        if len(node) == 2:
            res = '%s.%s' % (node[0], node[1])
        else:
            res = '%s.%s{oid=%s,isagg=%s}' % (
                self._schema, node[0], node[1], node[2])
        return res

    def _nodes_to_str(self, nodes):
        res = []
        for node in nodes:
            res.append(self._node_to_str(node))
        return res

    """
    @brief Get the dependency graph string for print
    """
    def get_dependency_graph_str(self):
        graph = self._build_dependency_graph(True)
        nodes = graph.keys()
        nodes.sort()
        res = '\t\tDependency Graph (Depender-Dependee Adjacency List):\n'
        for node in nodes:
            res += "\t\t%s -> %s\n" % (
                self._node_to_str(node), self._nodes_to_str(graph[node]))
        return res[:-1]

"""
@brief This class detects the table dependencies on MADLib UDTs defined in the
current version
"""
class TableDependency(UpgradeBase):
    def __init__(self, schema, portid, con_args):
        UpgradeBase.__init__(self, schema, portid, con_args)
        self._table2type = None
        self._detect_table_dependency()

    """
    @brief Detect the table dependencies on MADLib UDTs
    """
    def _detect_table_dependency(self):
        rows = self._run_sql("""
            SELECT
                nsp.nspname AS schema,
                relname AS relation,
                attname AS column,
                typname AS type
            FROM
                pg_attribute a,
                pg_class c,
                pg_type t,
                pg_namespace nsp
            WHERE
                t.typnamespace = {schema_madlib_oid}
                AND a.atttypid = t.oid
                AND c.oid = a.attrelid
                AND c.relnamespace = nsp.oid
                AND c.relkind = 'r'
            ORDER BY
                nsp.nspname, relname, attname, typname
            """.format(schema_madlib_oid=self._schema_oid))

        self._table2type = defaultdict(list)
        for row in rows:
            key= (row['schema'], row['relation'])
            self._table2type[key].append(
                (row['column'], row['type']))

    """
    @brief Check dependencies
    """
    def has_dependency(self):
        return len(self._table2type) > 0

    """
    @brief Get the list of depended UDTs
    """
    def get_depended_udt(self):
        res = defaultdict(bool)
        for table in self._table2type:
            for (col, typ) in self._table2type[table]:
                if typ not in res:
                    res[typ] = True
        return res

    """
    @brief Get the dependencies in string for print
    """
    def get_dependency_str(self):
        res = '\t\tTable Dependency (schema.table.column -> type):\n'
        for table in self._table2type:
            for (col, udt) in self._table2type[table]:
                res += "\t\t%s.%s.%s -> %s\n" % (table[0], table[1], col, udt)
        return res[:-1]

"""
@brief This class removes sql statements from a sql script which should not be
executed during the upgrade
"""
class ScriptCleaner(UpgradeBase):
    def __init__(self, schema, portid, con_args, change_handler):
        UpgradeBase.__init__(self, schema, portid, con_args)
        self._sql = None
        self._existing_uda = None
        self._existing_udt = None
        self._get_existing_uda()
        self._get_existing_udt()
        self._ch = change_handler

    """
    @breif Get the existing UDAs in the current version
    """
    def _get_existing_uda(self):
        rows = self._run_sql("""
            SELECT
                max(proname) AS proname,
                max(rettype) AS rettype,
                array_to_string(array_agg(argtype order by i), ',') AS argument
            FROM
            (
                SELECT
                    p.oid AS procoid,
                    proname,
                    textin(regtypeout(prorettype::regtype)) AS rettype,

                    CASE array_upper(proargtypes,1) WHEN -1 THEN ''
                        ELSE textin(regtypeout(unnest(proargtypes)::regtype))
                    END AS argtype,

                    CASE array_upper(proargtypes,1) WHEN -1 THEN 1
                        ELSE generate_series(0, array_upper(proargtypes, 1))
                    END AS i
                FROM
                    pg_proc AS p,
                    pg_namespace AS nsp
                WHERE
                    p.pronamespace = nsp.oid AND
                    p.proisagg = true AND
                    nsp.nspname = '{schema}'
            ) AS f
            GROUP BY
                procoid
            """.format(schema=self._schema))
        self._existing_uda = {}
        for row in rows:
            # Consider about the overloaded aggregates
            if row['proname'] not in self._existing_uda:
                self._existing_uda[row['proname']] = []
            self._existing_uda[row['proname']].append({
                'rettype': ['rettype'],
                'argument': row['argument']})

    """
    @brief Get the existing UDTs in the current version
    """
    def _get_existing_udt(self):
        rows = self._run_sql("""
            SELECT
                typname
            FROM
                pg_type AS t,
                pg_namespace AS nsp
            WHERE
                t.typnamespace = nsp.oid AND
                nsp.nspname = '{schema}'
            """.format(schema=self._schema))
        self._existing_udt = []
        for row in rows:
            self._existing_udt.append(row['typname'])

    """
    @note The changer_handler is needed for deciding which sql statements to
    remove
    """
    def get_change_handler(self):
        return self._ch

    """
    @brief Remove comments in the sql script
    """
    def _clean_comment(self):
        pattern = re.compile(r"""(/\*(.|[\r\n])*?\*/)|(--(.*|[\r\n]))""")
        res = ''
        lines = re.split(r'[\r\n]+', self._sql)
        for line in lines:
            tmp = line
            if not tmp.strip().startswith("E'"):
                line = re.sub(pattern, '', line)
            res += line + '\n'
        self._sql = res.strip()
        #self._sql = re.sub(pattern, '', self._sql).strip()

    """
    @breif Remove "drop/create type" statements in the sql script
    """
    def _clean_type(self):
        # remove 'drop type'
        pattern = re.compile('DROP(\s+)TYPE(.*?);', re.DOTALL | re.IGNORECASE);
        self._sql = re.sub(pattern, '', self._sql)

        # remove 'create type'
        udt_str = ''
        for udt in self._existing_udt:
            if udt in self._ch.get_udt():
                continue
            if udt_str == '':
                udt_str += udt
            else:
                udt_str += '|' + udt
        p_str = 'CREATE(\s+)TYPE(\s+)%s\.(%s)(.*?);' % (self._schema.upper(), udt_str)
        pattern = re.compile(p_str, re.DOTALL | re.IGNORECASE);
        self._sql = re.sub(pattern, '', self._sql)

    """
    @brief Remove "drop/create cast" statements in the sql script
    """
    def _clean_cast(self):
        # remove 'drop cast'
        pattern = re.compile('DROP(\s+)CAST(.*?);', re.DOTALL | re.IGNORECASE);
        self._sql = re.sub(pattern, '', self._sql)

        # remove 'create cast'
        udc_str = ''
        for udc in self._ch.get_udc():
            if udc_str == '':
                udc_str += '%s\s+AS\s+%s' % (
                    self._ch.get_udc()[udc]['sourcetype'], self._ch.get_udc()[udc]['targettype'])
            else:
                udc_str += '|' + '%s\s+AS\s+%s' % (
                    self._ch.get_udc()[udc]['sourcetype'], self._ch.get_udc()[udc]['targettype'])

        pattern = re.compile('CREATE\s+CAST(.*?);', re.DOTALL | re.IGNORECASE)
        if udc_str != '':
            pattern = re.compile('CREATE\s+CAST\s*\(\s*(?!%s)(.*?);' % udc_str , re.DOTALL | re.IGNORECASE)
        self._sql = re.sub(pattern, '', self._sql)

    """
    @brief Remove "drop/create operator" statements in the sql script
    """
    def _clean_operator(self):
        # remove 'drop operator'
        pattern = re.compile('DROP(\s+)OPERATOR(.*?);', re.DOTALL | re.IGNORECASE);
        self._sql = re.sub(pattern, '', self._sql)

        # remove 'create operator'
        pattern = re.compile(r"""CREATE(\s+)OPERATOR(.*?);""", re.DOTALL | re.IGNORECASE);
        self._sql = re.sub(pattern, '', self._sql)

    """
    @brief Rewrite the type
    """
    def _rewrite_type_in(self, arg):
        type_mapper = {
            'smallint':'(int2|smallint)',
            'integer':'(int|int4|integer)',
            'bigint':'(int8|bigint)',
            'double precision':'(float8|double precision)',
            'real':'(float4|real)',
            'character varying':'(varchar|character varying)'
        }
        for typ in type_mapper:
            arg = arg.replace(typ, type_mapper[typ])
        return arg.replace('[', '\[').replace(']', '\]')

    """
    @brief Remove "drop/create aggregate" statements in the sql script
    """
    def _clean_aggregate(self):
        # remove 'drop aggregate'
        pattern = re.compile('DROP(\s+)AGGREGATE(.*?);', re.DOTALL | re.IGNORECASE);
        self._sql = re.sub(pattern, '', self._sql)

        # remove 'create aggregate'
        uda_str = ''
        for uda in self._existing_uda:
            for item in self._existing_uda[uda]:
                if uda in self._ch.get_uda():
                    items = self._ch.get_uda()[uda]
                    if item in items:
                        continue
                p_arg_str = ''
                argument = item['argument']
                args = argument.split(',')
                for arg in args:
                    arg = self._rewrite_type_in(arg.strip())
                    if p_arg_str == '':
                        p_arg_str += '%s\s*' % arg
                    else:
                        p_arg_str += ',\s*%s\s*' % arg
                p_str = 'CREATE\s+(ORDERED\s)*\s*AGGREGATE\s+%s\.(%s)\s*\(\s*%s\)(.*?);' % (
                    self._schema.upper(), uda, p_arg_str)
                pattern = re.compile(p_str, re.DOTALL | re.IGNORECASE);
                self._sql = re.sub(pattern, '', self._sql)

    """
    @brief Remove "drop function" statements and rewrite "create function"
    statements in the sql script
    @note We don't drop any function
    """
    def _clean_function(self):
        # remove 'drop function'
        pattern = re.compile(r"""DROP(\s+)FUNCTION(.*?);""", re.DOTALL | re.IGNORECASE);
        self._sql = re.sub(pattern, '', self._sql)
        # replace 'create function' with 'create or replace function'
        pattern = re.compile(r"""CREATE(\s+)FUNCTION""", re.DOTALL | re.IGNORECASE);
        self._sql = re.sub(pattern, 'CREATE OR REPLACE FUNCTION', self._sql)

    """
    @brief Entry function for cleaning the sql script
    """
    def cleanup(self, sql):
        self._sql = sql
        self._clean_comment()
        self._clean_type()
        self._clean_cast()
        self._clean_operator()
        self._clean_aggregate()
        self._clean_function()
        return self._sql
