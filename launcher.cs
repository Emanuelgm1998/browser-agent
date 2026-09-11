using System;
using System.Diagnostics;

class ClineLauncher
{
    static int Main()
    {
        try
        {
            Console.Title = "Cline Chat";

            ProcessStartInfo psi = new ProcessStartInfo();
            psi.FileName = "cmd.exe";
            psi.Arguments = "/c call .venv\\Scripts\\activate && cline -i -P openai-native -m qwen3:8b \"Hola Emanuel, iniciar chat\"";
            psi.WorkingDirectory = @"C:\Users\Cloud\browser-agent";
            psi.UseShellExecute = false;

            Process p = Process.Start(psi);
            p.WaitForExit();

            if (p.ExitCode != 0)
            {
                Console.WriteLine();
                Console.WriteLine("Cline cerro con error (codigo " + p.ExitCode + ").");
                Console.WriteLine("Presiona una tecla para salir...");
                Console.ReadKey();
            }
            return p.ExitCode;
        }
        catch (Exception ex)
        {
            Console.WriteLine("Error al lanzar Cline: " + ex.Message);
            Console.WriteLine("Presiona una tecla para salir...");
            Console.ReadKey();
            return 1;
        }
    }
}