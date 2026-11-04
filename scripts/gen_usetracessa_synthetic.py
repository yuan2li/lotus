#!/usr/bin/env python3
"""Generate synthetic LLVM 14 IR (.ll) for the UseTraceSSA experiments.

Each program allocates --objects heap objects (one malloc site each) and
stores them into pointer slots, --aliases objects per slot, so a load from a
slot may point to k = --aliases objects under any flow-insensitive pointer
analysis. --sites relevant sites then load a slot and either free the pointer
or dereference it; --noise irrelevant integer instructions follow each
site, and every --branch-every sites the code forks into a diamond (a join for
history phis). Sites are split across --functions callees of main, so
summaries are exercised.

Uses:
    Table 3 (cost vs |U|):  --sweep-objects U1,U2,... --aliases K --sites-per-group 2
    Memory vs k:            --sites 100 --aliases K   (objects default to K)
    RQ2 n x k heat map:     --noise N --aliases K
    RQ4 random programs:    --random --seed S (random events, noise, branches)

The same file feeds lotus-ir-usetracessa, lotus-ir-ufg and lotus-check; one
program covers double-free, use-after-free and memory-leak (unfreed objects
reach the exit).

By default (--carrier struct) the slots are the fields of a struct that main
allocates on its stack and passes to every callee. --carrier global uses one
pointer global per slot instead; Saber (lotus-check) skips every allocation
whose value reaches a global, as SVF's Saber does, so it reports no
double-free or leak on those programs.

Objects are grouped k per slot, and site i uses group i mod (|U| / k), so a
group no site touches never reaches a free or dereference. --sites-per-group
sets --sites to that multiple of the group count, which keeps every object
relevant as |U| grows. --free-ratio applies to each group's uses in order, so
a group's first use frees it and later dereferences are uses after free.

Examples:
    python3 scripts/gen_usetracessa_synthetic.py --aliases 100 -o k100.ll
    python3 scripts/gen_usetracessa_synthetic.py --sweep-aliases 1,10,100,1000 \\
        --out-dir synthetic/
    python3 scripts/gen_usetracessa_synthetic.py --sweep-objects 10,100,1000 \\
        --sweep-aliases 1,10 --sites-per-group 2 --out-dir synthetic/
    python3 scripts/gen_usetracessa_synthetic.py --random --seed 7 -o r7.ll
"""
import argparse
from pathlib import Path
import random
import sys


def generate(sites=100, aliases=10, objects=None, noise=5, branch_every=10,
             functions=4, free_ratio=0.5, randomize=False, seed=0, carrier="struct"):
    rng = random.Random(seed)
    objects = max(objects or aliases, aliases)
    groups = (objects + aliases - 1) // aliases
    if randomize:
        noise_of = lambda: rng.randint(0, 2 * noise)
        event_of = lambda: rng.choice(("free", "deref", "deref", "none"))
        branch_of = lambda index: rng.random() < 1.0 / max(branch_every, 1)
        group_of = lambda index: rng.randrange(groups)
    else:
        noise_of = lambda: noise
        free_every = max(1, round(1 / free_ratio)) if free_ratio > 0 else 0
        # Count uses per group, so each group alternates free and dereference
        # (and has a use after free) whatever the group count's parity.
        event_of_index = lambda index: ("free" if free_every and
                                        (index // groups) % free_every == 0
                                        else "deref")
        branch_of = lambda index: branch_every > 0 and index % branch_every == branch_every - 1
        group_of = lambda index: index % groups

    lines = [
        f"; UseTraceSSA synthetic program: sites={sites} aliases={aliases} "
        f"objects={objects} noise={noise} branch_every={branch_every} "
        f"functions={functions} random={randomize} seed={seed}"
        + (f" carrier={carrier}" if carrier != "global" else ""),
        "declare i8* @malloc(i64)",
        "declare void @free(i8*)",
        "",
    ]
    if carrier == "global":
        lines += [f"@g{group} = internal global i8* null" for group in range(groups)]
        parameters, arguments = "", ""
    else:
        lines.append("%slots = type { " + ", ".join(["i8*"] * groups) + " }")
        parameters, arguments = "%slots* %s, ", "%slots* %s, "
    lines.append("")

    def slot(group, body, name):
        """Address of a group's slot; struct slots need a field GEP first."""
        if carrier == "global":
            return f"@g{group}"
        body.append(f"  %s{name} = getelementptr inbounds %slots, %slots* %s, "
                    f"i32 0, i32 {group}")
        return f"%s{name}"

    per_function = [sites // functions + (1 if f < sites % functions else 0)
                    for f in range(functions)]
    index = 0
    for function, count in enumerate(per_function):
        body = [f"define void @f{function}({parameters}i32 %x) {{", "entry:"]
        value, temp, block = "%x", 0, 0

        def emit_noise(amount):
            nonlocal value, temp
            for _ in range(amount):
                temp += 1
                op = ("add", "mul", "xor", "sub")[temp % 4]
                body.append(f"  %n{temp} = {op} i32 {value}, {temp % 7 + 1}")
                value = f"%n{temp}"

        for _ in range(count):
            event = event_of() if randomize else event_of_index(index)
            temp += 1
            pointer = f"%p{temp}"
            if event != "none":
                address = slot(group_of(index), body, temp)
                body.append(f"  {pointer} = load i8*, i8** {address}")
            if event == "free":
                body.append(f"  call void @free(i8* {pointer})")
            elif event == "deref":
                temp += 1
                body.append(f"  %v{temp} = load i8, i8* {pointer}")
            emit_noise(noise_of())
            if branch_of(index):
                block += 1
                temp += 1
                body.append(f"  %c{temp} = icmp slt i32 {value}, {temp}")
                body.append(f"  br i1 %c{temp}, label %then{block}, label %else{block}")
                before = value
                body.append(f"then{block}:")
                emit_noise(noise_of())
                then_value = value
                body.append(f"  br label %join{block}")
                body.append(f"else{block}:")
                value = before
                emit_noise(noise_of())
                else_value = value
                body.append(f"  br label %join{block}")
                body.append(f"join{block}:")
                temp += 1
                body.append(f"  %m{temp} = phi i32 [ {then_value}, %then{block} ], "
                            f"[ {else_value}, %else{block} ]")
                value = f"%m{temp}"
            index += 1
        body += ["  ret void", "}", ""]
        lines += body

    lines += ["define i32 @main() {", "entry:"]
    if carrier != "global":
        lines.append("  %s = alloca %slots")
    for obj in range(objects):
        lines.append(f"  %a{obj} = call i8* @malloc(i64 8)")
        address = slot(obj // aliases, lines, f"a{obj}")
        lines.append(f"  store i8* %a{obj}, i8** {address}")
    for function in range(functions):
        lines.append(f"  call void @f{function}({arguments}i32 {function + 1})")
    lines += ["  ret i32 0", "}", ""]
    return "\n".join(lines)


def int_list(text):
    return [int(part) for part in text.split(",") if part]


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--sites", type=int, default=100,
                        help="Relevant (free/dereference) sites (default: 100)")
    parser.add_argument("--aliases", type=int, default=10,
                        help="Objects a relevant pointer may alias, k (default: 10)")
    parser.add_argument("--objects", type=int,
                        help="Allocation sites, |U| (default: k; at least k)")
    parser.add_argument("--noise", type=int, default=5,
                        help="Irrelevant instructions after each site, n (default: 5)")
    parser.add_argument("--branch-every", type=int, default=10,
                        help="Sites between diamonds; 0 disables (default: 10)")
    parser.add_argument("--functions", type=int, default=4,
                        help="Callees of main that hold the sites (default: 4)")
    parser.add_argument("--free-ratio", type=float, default=0.5,
                        help="Fraction of sites that free (default: 0.5)")
    parser.add_argument("--random", action="store_true",
                        help="Random events, noise, branches and aliases (RQ4)")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--carrier", choices=("struct", "global"), default="struct",
                        help="Slots holding the objects: fields of a stack struct "
                             "passed to the callees (default), or pointer globals")
    parser.add_argument("-o", "--output", type=Path, help="Output .ll (default: stdout)")
    parser.add_argument("--sites-per-group", type=int,
                        help="Set --sites to this many sites per group of k objects, "
                             "so every object is used")
    parser.add_argument("--sweep-aliases", type=int_list,
                        help="Write one program per k into --out-dir")
    parser.add_argument("--sweep-objects", type=int_list,
                        help="Write one program per |U| into --out-dir")
    parser.add_argument("--sweep-noise", type=int_list,
                        help="Write one program per n into --out-dir")
    parser.add_argument("--out-dir", type=Path)
    args = parser.parse_args()
    if min(args.sites, args.aliases, args.functions) < 1 or args.noise < 0:
        parser.error("sites, aliases and functions must be positive; noise nonnegative")
    if args.sites_per_group is not None and args.sites_per_group < 1:
        parser.error("--sites-per-group must be positive")
    options = dict(branch_every=args.branch_every, functions=args.functions,
                   free_ratio=args.free_ratio, randomize=args.random, seed=args.seed,
                   carrier=args.carrier)

    def sites_for(aliases, objects):
        if args.sites_per_group is None:
            return args.sites
        groups = (max(objects or aliases, aliases) + aliases - 1) // aliases
        return groups * args.sites_per_group

    if args.sweep_aliases or args.sweep_objects or args.sweep_noise:
        if not args.out_dir:
            parser.error("--sweep-* requires --out-dir")
        args.out_dir.mkdir(parents=True, exist_ok=True)
        for noise in args.sweep_noise or [args.noise]:
            for objects in args.sweep_objects or [args.objects]:
                for aliases in args.sweep_aliases or [args.aliases]:
                    if objects is not None and objects < aliases:
                        continue  # |U| >= k: the cell would repeat |U| = k
                    sites = sites_for(aliases, objects)
                    universe = f"-u{objects}" if objects is not None else ""
                    name = f"syn-s{sites}-k{aliases}{universe}-n{noise}.ll"
                    (args.out_dir / name).write_text(
                        generate(sites=sites, aliases=aliases, objects=objects,
                                 noise=noise, **options))
        return 0
    text = generate(sites=sites_for(args.aliases, args.objects), aliases=args.aliases,
                    objects=args.objects, noise=args.noise, **options)
    if args.output:
        args.output.write_text(text)
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
