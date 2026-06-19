#!/usr/bin/env python3
import os
import sys
import subprocess
import argparse
import curses
import threading
import queue
import time

# --- COLOR CONSTANTS ---
CP_DEFAULT = 0
CP_GREEN = 1
CP_RED = 2
CP_YELLOW = 3
CP_CYAN = 4
CP_MAGENTA = 5

def init_colors():
    if curses.has_colors():
        curses.start_color()
        curses.use_default_colors()
        curses.init_pair(CP_GREEN, curses.COLOR_GREEN, -1)
        curses.init_pair(CP_RED, curses.COLOR_RED, -1)
        curses.init_pair(CP_YELLOW, curses.COLOR_YELLOW, -1)
        curses.init_pair(CP_CYAN, curses.COLOR_CYAN, -1)
        curses.init_pair(CP_MAGENTA, curses.COLOR_MAGENTA, -1)

# --- GIT HELPER FUNCTIONS ---

def run_git(args, cwd=None):
    try:
        result = subprocess.run(
            ['git'] + args,
            cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False
        )
        return result.stdout.strip()
    except Exception as e:
        return str(e)

def get_submodule_stats(full_path):
    if not os.path.isdir(os.path.join(full_path, ".git")) and not os.path.isfile(os.path.join(full_path, ".git")):
        return [], ""
        
    status_output = run_git(['status', '--porcelain'], cwd=full_path)
    files = []
    for line in status_output.splitlines():
        if len(line) >= 4:
            status = line[:2]
            filename = line[3:]
            files.append({'status': status, 'name': filename})

    shortstat = run_git(['diff', '--shortstat'], cwd=full_path)
    insertions, deletions = 0, 0
    if shortstat:
        for part in shortstat.split(','):
            if 'insertion' in part:
                insertions = int(''.join(filter(str.isdigit, part)))
            elif 'deletion' in part:
                deletions = int(''.join(filter(str.isdigit, part)))
                
    stat_str = ""
    if insertions or deletions:
        stat_str = f"+{insertions}/-{deletions}"
    elif files:
        stat_str = f"~{len(files)} files"
        
    return files, stat_str

def get_submodules(base_dir, show_all=False):
    output = run_git(['submodule', 'status', '--recursive'], cwd=base_dir)
    raw_subs = []
    if not output: return []
        
    for line in output.splitlines():
        if not line: continue
        status_char = line[0] 
        parts = line[1:].strip().split()
        commit_hash, path = parts[0], parts[1]
        full_path = os.path.join(base_dir, path)
        
        files, stat_str = get_submodule_stats(full_path)
        has_changes = (status_char in ['+', '-', 'U']) or bool(files)
        
        raw_subs.append({
            'path': path, 'commit': commit_hash,
            'needs_update': status_char == '+', 'uninitialized': status_char == '-',
            'dirty': bool(files), 'has_changes': has_changes,
            'stat_str': stat_str, 'files': files, 'full_path': full_path
        })

    if show_all: return raw_subs

    active_paths = {sm['path'] for sm in raw_subs if sm['has_changes']}
    filtered_subs = []
    for sm in raw_subs:
        is_parent_of_active = any(act.startswith(sm['path'] + '/') for act in active_paths)
        if sm['has_changes'] or is_parent_of_active:
            filtered_subs.append(sm)
            
    return filtered_subs

def cascade_commit(base_dir, sub_path, commit_msg, target_file=None):
    parts = sub_path.split('/')
    target_dir = os.path.join(base_dir, sub_path)
    
    # If targeting a specific file, only stage that file
    if target_file:
        run_git(['add', target_file], cwd=target_dir)
    else:
        run_git(['add', '.'], cwd=target_dir)
        
    run_git(['commit', '-m', commit_msg], cwd=target_dir)
    run_git(['push'], cwd=target_dir)
    
    paths_to_commit = ["/".join(parts[:i+1]) for i in range(len(parts))]
    paths_to_commit.reverse()
    
    for path in paths_to_commit:
        parent_dir = base_dir if '/' not in path else os.path.join(base_dir, os.path.dirname(path))
        module_name = os.path.basename(path)
        
        run_git(['add', module_name], cwd=parent_dir)
        msg = f"Update submodule {module_name}\n\nCascaded from: {commit_msg}"
        run_git(['commit', '-m', msg], cwd=parent_dir)
        run_git(['push'], cwd=parent_dir)

# --- TREE CONSTRUCTORS ---

def build_tree(submodules):
    tree = {}
    for sm in submodules:
        parts = sm['path'].split('/')
        current = tree
        for part in parts[:-1]:
            if part not in current:
                current[part] = {'_meta': None, '_children': {}}
            current = current[part]['_children']
        if parts[-1] not in current:
            current[parts[-1]] = {'_meta': sm, '_children': {}}
        else:
            current[parts[-1]]['_meta'] = sm
    return tree

def flatten_tree(tree, prefix=""):
    lines = []
    keys = list(tree.keys())
    for i, key in enumerate(keys):
        is_last = (i == len(keys) - 1)
        node = tree[key]
        meta = node['_meta']
        
        branch = "└─ " if is_last else "├─ "
        display = f"{prefix}{branch}{key}"
                
        lines.append({'display': display, 'meta': meta})
        if node['_children']:
            next_prefix = prefix + ("   " if is_last else "│  ")
            lines.extend(flatten_tree(node['_children'], next_prefix))
    return lines

# --- BACKGROUND WORKER ---

def background_job(action, base_dir, meta, msg, result_queue, target_file=None):
    """Executes long-running git tasks and builds the new tree in the background."""
    try:
        if action == 'update':
            run_git(['submodule', 'update', '--init', meta['path']], cwd=base_dir)
            
        elif action == 'clean':
            if target_file:
                # Discard tracked changes
                run_git(['checkout', 'HEAD', '--', target_file], cwd=meta['full_path'])
                # Remove untracked file
                run_git(['clean', '-f', '--', target_file], cwd=meta['full_path'])
            else:
                run_git(['clean', '-xdf'], cwd=meta['full_path'])
                
        elif action == 'stash':
            if target_file:
                run_git(['stash', 'push', '--', target_file], cwd=meta['full_path'])
            else:
                run_git(['stash'], cwd=meta['full_path'])
                
        elif action == 'commit':
            cascade_commit(base_dir, meta['path'], msg, target_file)
            
        # Rebuild tree asynchronously so UI doesn't stutter
        new_subs = get_submodules(base_dir, show_all=show_all_flag)
        new_nodes = flatten_tree(build_tree(new_subs))
        
        result_queue.put({
            'status': 'success',
            'path': meta['path'],
            'nodes': new_nodes
        })
    except Exception as e:
        result_queue.put({'status': 'error', 'path': meta['path'], 'error': str(e)})

# --- TUI DRAW ENGINE ---

def prompt_user(stdscr, prompt_text):
    h, w = stdscr.getmaxyx()
    win = curses.newwin(3, w-4, h//2 - 1, 2)
    win.box()
    win.addstr(1, 1, prompt_text, curses.color_pair(CP_CYAN) | curses.A_BOLD)
    stdscr.refresh()
    win.refresh()
    curses.echo()
    curses.curs_set(1)
    stdscr.nodelay(0) # Temporarily block for typing
    user_input = win.getstr(1, len(prompt_text) + 2).decode('utf-8')
    stdscr.nodelay(1) # Return to non-blocking
    curses.noecho()
    curses.curs_set(0)
    return user_input

def draw_ui(stdscr, base_dir, nodes, selected_idx, file_idx, focus, diff_scroll, active_jobs, spinner_char):
    stdscr.erase()
    h, w = stdscr.getmaxyx()
    left_w = int(w * 0.4)
    right_w = w - left_w
    
    # Left Pane Header
    tree_title = f" Submodules Tree ({'ALL' if show_all_flag else 'CHANGED'}) "
    header_attr = curses.color_pair(CP_CYAN) | curses.A_REVERSE if focus == 'left' else curses.A_REVERSE
    stdscr.addstr(0, 0, tree_title.ljust(left_w), header_attr)
    
    # Render Left Tree Pane
    for i, node in enumerate(nodes):
        if i >= h - 3: break
        base_text = node['display']
        meta = node['meta']
        
        # Append status flags to display string
        display_str = base_text
        row_attr = curses.A_NORMAL
        
        if meta:
            is_active = meta['path'] in active_jobs
            
            if is_active:
                display_str += f" [{spinner_char} Working...]"
                row_attr = curses.color_pair(CP_CYAN) | curses.A_BOLD
            else:
                flags = []
                if meta['stat_str']: flags.append(f"[{meta['stat_str']}]")
                if meta['needs_update']: flags.append("[Needs Update]")
                if meta['uninitialized']: flags.append("[Uninitialized]")
                if flags:
                    display_str += f" {' '.join(flags)}"
                
                if meta['needs_update'] or meta['uninitialized']:
                    row_attr = curses.color_pair(CP_RED)
                elif meta['dirty']:
                    row_attr = curses.color_pair(CP_YELLOW)
                else:
                    row_attr = curses.color_pair(CP_GREEN)
                
        # Truncate strictly for left pane width
        display_str = display_str[:left_w-1]
                
        if i == selected_idx:
            row_attr |= (curses.A_REVERSE if focus == 'left' else curses.A_STANDOUT)
            
        stdscr.addstr(i+1, 0, display_str.ljust(left_w), row_attr)
            
    # Vertical Separation Borders
    for y in range(h-1):
        try: stdscr.addch(y, left_w, curses.ACS_VLINE, curses.color_pair(CP_CYAN))
        except curses.error: pass

    # Bounds check selected index
    selected_idx = min(selected_idx, len(nodes) - 1)
    meta = nodes[selected_idx]['meta'] if nodes else None
    
    # Right Pane Header
    right_title = " Details & File Diffs "
    header_attr = curses.color_pair(CP_CYAN) | curses.A_REVERSE if focus == 'right' else curses.A_REVERSE
    stdscr.addstr(0, left_w + 1, right_title.ljust(right_w - 1), header_attr)

    if meta:
        stdscr.addstr(1, left_w + 2, f"Path: {meta['path']}"[:right_w-4], curses.A_BOLD | curses.color_pair(CP_MAGENTA))
        stdscr.addstr(2, left_w + 2, f"Commit: {meta['commit']}"[:right_w-4])
        stdscr.addstr(4, left_w + 2, "CHANGED FILES:", curses.A_UNDERLINE | curses.color_pair(CP_CYAN))
        
        file_list_height = 6
        files = meta['files']
        
        # Render File List Box
        for f_idx, f in enumerate(files):
            if f_idx >= file_list_height: break
            f_text = f"  {f['status']} {f['name']}"[:right_w-4]
            
            f_attr = curses.A_NORMAL
            if 'M' in f['status']: f_attr = curses.color_pair(CP_YELLOW)
            elif 'A' in f['status'] or '?' in f['status']: f_attr = curses.color_pair(CP_GREEN)
            elif 'D' in f['status']: f_attr = curses.color_pair(CP_RED)
            
            if f_idx == file_idx:
                f_attr |= (curses.A_REVERSE if focus == 'right' else curses.A_STANDOUT)
                
            stdscr.addstr(5 + f_idx, left_w + 2, f_text.ljust(right_w-4), f_attr)
                
        if not files:
            stdscr.addstr(5, left_w + 4, "(No working directory changes)", curses.A_DIM)
            
        sep_y = 5 + file_list_height + 1
        try: stdscr.addstr(sep_y, left_w + 1, "─" * (right_w - 1), curses.color_pair(CP_CYAN))
        except curses.error: pass
        
        # Gather Diff View Information
        diff_lines = []
        if meta['path'] in active_jobs:
            diff_lines.append("... Operation in progress, diff temporarily unavailable ...")
        else:
            if files and file_idx < len(files):
                target_file = files[file_idx]['name']
                raw_diff = run_git(['diff', target_file], cwd=meta['full_path'])
                if raw_diff: diff_lines.extend(raw_diff.splitlines())
                else:
                    raw_diff = run_git(['diff', '--no-index', '/dev/null', target_file], cwd=meta['full_path'])
                    if not raw_diff or "Error" in raw_diff: diff_lines.append("(Untracked or binary data modifications)")
                    else: diff_lines.extend(raw_diff.splitlines())
            else:
                raw_diff = run_git(['diff'], cwd=meta['full_path'])
                if raw_diff: diff_lines.extend(raw_diff.splitlines())
                else: diff_lines.append("No active line differences detected.")

        # Stream Diff Data with Syntax Highlighting
        diff_box_start = sep_y + 1
        diff_box_height = (h - 2) - diff_box_start
        
        for d_idx, line in enumerate(diff_lines[diff_scroll:]):
            if d_idx >= diff_box_height: break
            clippable_line = line[:right_w-4]
            
            line_attr = curses.A_NORMAL
            if clippable_line.startswith('+') and not clippable_line.startswith('+++'):
                line_attr = curses.color_pair(CP_GREEN)
            elif clippable_line.startswith('-') and not clippable_line.startswith('---'):
                line_attr = curses.color_pair(CP_RED)
            elif clippable_line.startswith('@@'):
                line_attr = curses.color_pair(CP_CYAN)
            elif clippable_line.startswith('diff') or clippable_line.startswith('index'):
                line_attr = curses.A_BOLD
                
            try: stdscr.addstr(diff_box_start + d_idx, left_w + 2, clippable_line, line_attr)
            except curses.error: pass
    else:
        stdscr.addstr(2, left_w + 2, "Directory Tree Node (No Git Meta Definition)", curses.A_DIM)

    # Bottom Bar
    bar = "[TAB] Switch Panel | [↑/↓] Nav | [PgUp/PgDn] Diff | [U]pdate | [X] Clean | [S]tash | [C]ommit | [Q]uit"
    stdscr.addstr(h-1, 0, bar[:w-1], curses.A_REVERSE | curses.color_pair(CP_CYAN))
    
    # Optional active job indicator on right edge
    if active_jobs:
        job_txt = f" Active Jobs: {len(active_jobs)} {spinner_char} "
        stdscr.addstr(h-1, w - len(job_txt) - 1, job_txt, curses.A_REVERSE | curses.color_pair(CP_YELLOW))
        
    stdscr.refresh()

def main_tui(stdscr, base_dir):
    global show_all_flag
    init_colors()
    curses.curs_set(0)
    stdscr.nodelay(1)  # Make getch non-blocking
    stdscr.timeout(100) # Update loop runs every 100ms
    
    # Initial loading screen
    stdscr.clear()
    stdscr.addstr(0, 0, "Scanning git submodules, please wait...", curses.color_pair(CP_CYAN))
    stdscr.refresh()
    
    submodules_list = get_submodules(base_dir, show_all=show_all_flag)
    nodes = flatten_tree(build_tree(submodules_list))
    
    selected_idx, file_idx, diff_scroll = 0, 0, 0
    focus = 'left' 
    
    active_jobs = {} # dict mapping target path -> thread
    res_queue = queue.Queue()
    spinner_chars = ['|', '/', '-', '\\']
    spinner_idx = 0

    while True:
        spinner_char = spinner_chars[spinner_idx % len(spinner_chars)]
        
        draw_ui(stdscr, base_dir, nodes, selected_idx, file_idx, focus, diff_scroll, active_jobs, spinner_char)
        
        # Check background queue for completed jobs
        try:
            while True:
                res = res_queue.get_nowait()
                path = res['path']
                if path in active_jobs:
                    del active_jobs[path]
                if res['status'] == 'success':
                    nodes = res['nodes']
                    # Keep selected index in bounds after tree refresh
                    selected_idx = min(selected_idx, max(0, len(nodes) - 1))
        except queue.Empty:
            pass

        key = stdscr.getch()
        spinner_idx += 1

        if key == -1: # No input received in timeout window
            continue

        if key == 9: # TAB
            if focus == 'left':
                meta = nodes[selected_idx]['meta'] if nodes else None
                if meta and meta['files']: 
                    focus, file_idx, diff_scroll = 'right', 0, 0
            else:
                focus, diff_scroll = 'left', 0
                
        elif key == curses.KEY_UP:
            if focus == 'left' and selected_idx > 0:
                selected_idx -= 1
                file_idx = 0
                diff_scroll = 0
            elif focus == 'right' and file_idx > 0:
                file_idx -= 1
                diff_scroll = 0
                    
        elif key == curses.KEY_DOWN:
            if focus == 'left' and selected_idx < len(nodes) - 1:
                selected_idx += 1
                file_idx = 0
                diff_scroll = 0
            elif focus == 'right':
                meta = nodes[selected_idx]['meta'] if nodes else None
                if meta and file_idx < len(meta['files']) - 1:
                    file_idx += 1
                    diff_scroll = 0
                    
        elif key in (curses.KEY_PPAGE, ord('-')): 
            diff_scroll = max(0, diff_scroll - 5)
        elif key in (curses.KEY_NPAGE, ord('=')): 
            diff_scroll += 5
        elif key in (ord('q'), ord('Q')): 
            break
            
        meta = nodes[selected_idx]['meta'] if nodes else None
        if not meta: continue
        
        # Action Triggers
        if key in (ord('u'), ord('U'), ord('x'), ord('X'), ord('s'), ord('S'), ord('c'), ord('C')):
            if meta['path'] in active_jobs:
                continue # Block multiple actions on the same submodule
            
            action = None
            msg = None
            
            # Determine Context: Are we acting on a specific file?
            target_file = None
            if focus == 'right' and meta.get('files') and file_idx < len(meta['files']):
                target_file = meta['files'][file_idx]['name']

            if key in (ord('u'), ord('U')): 
                action = 'update' # Update usually applies to the whole submodule regardless
            elif key in (ord('x'), ord('X')): 
                action = 'clean'
            elif key in (ord('s'), ord('S')): 
                action = 'stash'
            elif key in (ord('c'), ord('C')):
                prompt_prefix = f"Commit {os.path.basename(target_file)}: " if target_file else "Commit Message: "
                msg = prompt_user(stdscr, prompt_prefix)
                if msg: action = 'commit'
                
            if action:
                t = threading.Thread(target=background_job, args=(action, base_dir, meta, msg, res_queue, target_file))
                t.daemon = True
                active_jobs[meta['path']] = t
                t.start()
                
                # Push focus back to the left pane since the file list will shift/refresh
                if focus == 'right': 
                    focus = 'left'

# --- ENTRY SYSTEM ---
show_all_flag = False

def main():
    global show_all_flag
    parser = argparse.ArgumentParser(description="GitSubmerge: TUI for deeply nested submodules.")
    parser.add_argument("directory", nargs="?", default=".", help="Base path directory of the Git Superproject")
    parser.add_argument("-u", "--update", action="store_true", help="Runs top level submodule init update chains beforehand")
    parser.add_argument("-a", "--all", action="store_true", help="Display all submodules instead of omitting clean ones")
    args = parser.parse_args()

    target_dir = os.path.abspath(args.directory)
    show_all_flag = args.all

    if not os.path.isdir(os.path.join(target_dir, ".git")):
        print(f"Critical Error: Execution directory '{target_dir}' is not an initialized Git hub repository.")
        sys.exit(1)

    if args.update:
        print("Executing comprehensive initial submodule update sequences...")
        run_git(['submodule', 'update', '--init', '--recursive'], cwd=target_dir)

    try:
        curses.wrapper(main_tui, target_dir)
    except KeyboardInterrupt:
        pass

if __name__ == "__main__":
    main()
