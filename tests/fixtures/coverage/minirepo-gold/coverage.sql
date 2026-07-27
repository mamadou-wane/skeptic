PRAGMA foreign_keys=OFF;
BEGIN TRANSACTION;
CREATE TABLE coverage_schema (
    -- One row, to record the version of the schema in this db.
    version integer
);
INSERT INTO coverage_schema VALUES(7);
CREATE TABLE meta (
    -- Key-value pairs, to record metadata about the data
    key text,
    value text,
    unique (key)
    -- Possible keys:
    --  'has_arcs' boolean      -- Is this data recording branches?
    --  'sys_argv' text         -- The coverage command line that recorded the data.
    --  'version' text          -- The version of coverage.py that made the file.
    --  'when' text             -- Datetime when the file was created.
    --  'hash' text             -- Hash of the data.
);
INSERT INTO meta VALUES('version','7.15.2');
INSERT INTO meta VALUES('has_arcs','0');
CREATE TABLE file (
    -- A row per file measured.
    id integer primary key,
    path text,
    unique (path)
);
INSERT INTO file VALUES(1,'conftest.py');
INSERT INTO file VALUES(2,'tests/test_golden.py');
INSERT INTO file VALUES(3,'minirepo.py');
INSERT INTO file VALUES(4,'tests/test_minirepo.py');
CREATE TABLE context (
    -- A row per context measured.
    id integer primary key,
    context text,
    unique (context)
);
INSERT INTO context VALUES(1,'');
INSERT INTO context VALUES(2,'test_golden.test_golden_render_matches_expected');
INSERT INTO context VALUES(3,'test_minirepo.test_parse_range_basic');
INSERT INTO context VALUES(4,'test_minirepo.test_parse_range_wide');
INSERT INTO context VALUES(5,'test_minirepo.test_clamp_bounds');
CREATE TABLE line_bits (
    -- If recording lines, a row per context per file executed.
    -- All of the line numbers for that file/context are in one numbits.
    file_id integer,            -- foreign key to `file`.
    context_id integer,         -- foreign key to `context`.
    numbits blob,               -- see the numbits functions in coverage.numbits
    foreign key (file_id) references file (id),
    foreign key (context_id) references context (id),
    unique (file_id, context_id)
);
INSERT INTO line_bits VALUES(1,1,X'01');
INSERT INTO line_bits VALUES(2,1,X'022a11');
INSERT INTO line_bits VALUES(3,1,X'1202');
INSERT INTO line_bits VALUES(4,1,X'1211');
INSERT INTO line_bits VALUES(2,2,X'000022');
INSERT INTO line_bits VALUES(3,2,X'60');
INSERT INTO line_bits VALUES(3,3,X'60');
INSERT INTO line_bits VALUES(4,3,X'20');
INSERT INTO line_bits VALUES(3,4,X'60');
INSERT INTO line_bits VALUES(4,4,X'0002');
INSERT INTO line_bits VALUES(3,5,X'0004');
INSERT INTO line_bits VALUES(4,5,X'00e0');
CREATE TABLE arc (
    -- If recording branches, a row per context per from/to line transition executed.
    file_id integer,            -- foreign key to `file`.
    context_id integer,         -- foreign key to `context`.
    fromno integer,             -- line number jumped from.
    tono integer,               -- line number jumped to.
    foreign key (file_id) references file (id),
    foreign key (context_id) references context (id),
    unique (file_id, context_id, fromno, tono)
);
CREATE TABLE tracer (
    -- A row per file indicating the tracer used for that file.
    file_id integer primary key,
    tracer text,
    foreign key (file_id) references file (id)
);
COMMIT;
