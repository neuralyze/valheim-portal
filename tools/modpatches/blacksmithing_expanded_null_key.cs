// Guard BlacksmithingItemData.Load against a null m_shared key.
//
// The unpatched method does, unconditionally:
//
//   ldsfld   Dictionary<ItemDrop/ItemData/SharedData, BlacksmithingItemData>
//   ldarg.0
//   call     ItemDataManager.ItemData::get_Item()
//   ldfld    ItemDrop/ItemData::m_shared        <- key
//   ldarg.0                                     <- value
//   callvirt Dictionary::set_Item(key, value)
//
// `m_shared` is null exactly when the game could not resolve the item's prefab
// ("Failed to find item prefab <hash>" - an item left behind by an uninstalled
// mod). Dictionary.set_Item then throws ArgumentNullException, and because this
// runs inside ZDOMan.ConvertContainers during the one-time Valheim 1.0 world
// conversion, the exception aborts the ENTIRE world load: the server prints
// "World load failed mid-file. Exiting without save." and quits. Measured on
// Vangard 2026-09-13: 451,451 zdos read, then abort, in a restart loop.
//
// The patch prepends a null test on the same key and branches past the insert,
// so an unresolvable item is simply not registered - which is the only sensible
// behaviour, since there is no shared data to key it by.
using System;
using System.Linq;
using Mono.Cecil;
using Mono.Cecil.Cil;

static class P
{
    static int Main(string[] a)
    {
        var resolver = new DefaultAssemblyResolver();
        resolver.AddSearchDirectory(System.IO.Path.GetDirectoryName(System.IO.Path.GetFullPath(a[0])));
        var asm = AssemblyDefinition.ReadAssembly(a[0], new ReaderParameters { AssemblyResolver = resolver });

        var type = asm.MainModule.GetTypes()
            .FirstOrDefault(t => t.Name == "BlacksmithingItemData");
        if (type == null) { Console.Error.WriteLine("BlacksmithingItemData not found"); return 2; }

        var load = type.Methods.FirstOrDefault(m => m.Name == "Load" && m.Parameters.Count == 0);
        if (load == null || !load.HasBody) { Console.Error.WriteLine("Load() not found"); return 2; }

        var il = load.Body.GetILProcessor();
        var ins = load.Body.Instructions;

        var set = ins.FirstOrDefault(i => i.OpCode == OpCodes.Callvirt
            && i.Operand is MethodReference mr && mr.Name == "set_Item"
            && mr.DeclaringType.Name.StartsWith("Dictionary"));
        if (set == null) { Console.Error.WriteLine("Dictionary::set_Item call not found"); return 3; }

        int si = ins.IndexOf(set);
        // Verify the exact shape before rewriting anything; refuse on any surprise.
        var start = ins[si - 5];
        if (start.OpCode != OpCodes.Ldsfld
            || ins[si - 4].OpCode != OpCodes.Ldarg_0
            || ins[si - 3].OpCode != OpCodes.Call
            || ins[si - 2].OpCode != OpCodes.Ldfld
            || ins[si - 1].OpCode != OpCodes.Ldarg_0)
        {
            Console.Error.WriteLine("unexpected IL shape - refusing to patch");
            for (int k = si - 5; k <= si; k++) Console.Error.WriteLine("  " + ins[k]);
            return 4;
        }

        var getItem = (MethodReference)ins[si - 3].Operand;
        var shared = (FieldReference)ins[si - 2].Operand;
        var after = set.Next;                       // resume point: the nop after the insert
        if (after == null) { Console.Error.WriteLine("no instruction after set_Item"); return 5; }

        var g1 = il.Create(OpCodes.Ldarg_0);
        var g2 = il.Create(OpCodes.Call, getItem);
        var g3 = il.Create(OpCodes.Ldfld, shared);
        var g4 = il.Create(OpCodes.Brfalse, after);

        il.InsertBefore(start, g1);
        il.InsertBefore(start, g2);
        il.InsertBefore(start, g3);
        il.InsertBefore(start, g4);

        // Short branches elsewhere in the method now span more bytes than they can encode.
        foreach (var i in load.Body.Instructions.ToList())
        {
            if (i.OpCode == OpCodes.Brfalse_S) i.OpCode = OpCodes.Brfalse;
            else if (i.OpCode == OpCodes.Brtrue_S) i.OpCode = OpCodes.Brtrue;
            else if (i.OpCode == OpCodes.Br_S) i.OpCode = OpCodes.Br;
        }

        asm.Write(a[1]);
        Console.WriteLine("patched: null m_shared now skips the dictionary insert");
        return 0;
    }
}
