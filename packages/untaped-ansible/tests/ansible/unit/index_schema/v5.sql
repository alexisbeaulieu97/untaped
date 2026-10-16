
create table if not exists source_runs (
    source_key text primary key,
    scanned_at text not null,
    repos integer not null default 0,
    refs integer not null default 0,
    edges integer not null default 0
);

create table if not exists dependency_snapshots (
    id integer primary key autoincrement,
    source_repo text not null,
    source_sha text,
    dependency_paths_fingerprint text not null,
    aliases_fingerprint text not null default '',
    unique(source_repo, source_sha, dependency_paths_fingerprint, aliases_fingerprint)
);

create table if not exists snapshot_edges (
    id integer primary key autoincrement,
    snapshot_id integer not null references dependency_snapshots(id) on delete cascade,
    dependency_repo text,
    dependency_repo_key text,
    dependency_name text not null,
    dependency_version text,
    source_path text not null,
    unresolved text
);

create table if not exists source_ref_scans (
    id integer primary key autoincrement,
    source_key text not null,
    source_repo text not null,
    source_repo_key text not null,
    ref_kind text not null,
    source_ref text not null,
    source_sha text not null,
    clone_url text,
    clone_protocol text,
    dependency_paths_fingerprint text not null,
    aliases_fingerprint text not null default '',
    checked_at text not null,
    indexed_at text not null,
    snapshot_id integer not null references dependency_snapshots(id),
    unique(source_key, source_repo, ref_kind, source_ref)
);

create table if not exists source_repo_metadata (
    source_key text not null,
    source_repo text not null,
    source_repo_key text not null,
    default_branch text not null,
    primary key (source_key, source_repo)
);

create table if not exists source_refresh_progress (
    source_key text not null,
    source_fingerprint text not null,
    source_repo text not null,
    status text not null,
    updated_at text not null,
    primary key (source_key, source_fingerprint, source_repo)
);

create index if not exists idx_dependency_snapshots_identity
    on dependency_snapshots(
        source_repo, source_sha, dependency_paths_fingerprint, aliases_fingerprint
    );
create index if not exists idx_snapshot_edges_dependency
    on snapshot_edges(snapshot_id, dependency_repo, dependency_version);
create index if not exists idx_snapshot_edges_dependency_ref
    on snapshot_edges(dependency_repo_key, dependency_version, snapshot_id);
create index if not exists idx_source_ref_scans_source
    on source_ref_scans(source_key, source_repo, ref_kind, source_ref);
create index if not exists idx_source_ref_scans_source_ref
    on source_ref_scans(source_key, source_repo_key, source_ref);
create index if not exists idx_source_ref_scans_snapshot
    on source_ref_scans(snapshot_id);
create index if not exists idx_source_ref_scans_source_snapshot
    on source_ref_scans(source_key, snapshot_id);
create index if not exists idx_source_repo_metadata_source
    on source_repo_metadata(source_key, source_repo_key);
create index if not exists idx_source_refresh_progress_source
    on source_refresh_progress(source_key, source_fingerprint, source_repo);

create table if not exists variable_files (
    id integer primary key,
    blob text not null,
    path text not null,
    analyser text not null,
    skipped text,
    unique(blob, path)
);

create table if not exists variable_facts (
    file_id integer not null references variable_files(id),
    name text not null,
    how integer not null,
    line integer not null,
    key_path text not null
);

create table if not exists variable_listing (
    source_repo_key text not null,
    source_sha text not null,
    file_id integer not null references variable_files(id),
    primary key (source_repo_key, source_sha, file_id)
);

create table if not exists variable_commits (
    source_repo_key text not null,
    source_sha text not null,
    analyser text not null,
    primary key (source_repo_key, source_sha)
);

create index if not exists idx_variable_facts_name
    on variable_facts(name, file_id);
create index if not exists idx_variable_facts_file
    on variable_facts(file_id);
create index if not exists idx_variable_listing_file
    on variable_listing(file_id);
