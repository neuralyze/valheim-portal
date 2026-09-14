using System; using System.IO; using System.Linq; using Mono.Cecil;
class P { static int Main(string[] a){
 var outDir=a[0];
 Directory.CreateDirectory(outDir);
 var res=new DefaultAssemblyResolver();
 foreach(var f in a.Skip(1)) res.AddSearchDirectory(Path.GetDirectoryName(Path.GetFullPath(f)));
 foreach(var f in a.Skip(1)){
  var asm=AssemblyDefinition.ReadAssembly(f,new ReaderParameters{AssemblyResolver=res});
  int tc=0,fc=0,mc=0;
  foreach(var t in asm.MainModule.GetTypes()){
   if(t.IsNested){ if(!t.IsNestedPublic){t.IsNestedPublic=true;tc++;} }
   else if(!t.IsPublic){t.IsPublic=true;tc++;}
   foreach(var fl in t.Fields) if(!fl.IsPublic){ fl.IsPublic=true; fc++; }
   foreach(var m in t.Methods) if(!m.IsPublic){ m.IsPublic=true; mc++; }
  }
  var name=Path.GetFileNameWithoutExtension(f)+"_publicized.dll";
  asm.Write(Path.Combine(outDir,name));
  Console.WriteLine($"{name}: types+{tc} fields+{fc} methods+{mc}");
 }
 return 0;}}
