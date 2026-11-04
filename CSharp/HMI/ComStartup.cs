using System;
using System.Diagnostics;
using System.IO;
using System.Threading.Tasks;
using SimHub2SimRig.Shared;

namespace SimHub2SimRig.HMI
{
    public static class ComStartup
    {
        public static async Task Run(Func<Task<Status>> query, Action launch, Func<Task> initialize, Func<bool> canceled)
        {
            Status status = null;
            if (canceled()) throw new OperationCanceledException();
            try { status = await query(); }
            catch (Exception e) { if (!ConnectionError(e)) throw; }
            if (canceled()) throw new OperationCanceledException();
            if (status == null)
            {
                launch(); var watch = Stopwatch.StartNew();
                while (status == null)
                {
                    if (canceled()) throw new OperationCanceledException();
                    try { status = await query(); }
                    catch (Exception e) { if (!ConnectionError(e)) throw; }
                    if (status == null)
                    {
                        if (watch.Elapsed.TotalSeconds >= 30) throw new TimeoutException("Com wurde gestartet, ist aber noch nicht erreichbar. Bitte die Com-Konsole prüfen.");
                        await Task.Delay(200);
                    }
                }
            }
            if (canceled()) throw new OperationCanceledException();
            if (!status.Initialized && !status.Busy) await initialize();
        }

        static bool ConnectionError(Exception error)
        {
            var aggregate = error as AggregateException;
            if (aggregate != null) error = aggregate.GetBaseException();
            return error is IOException || error is TimeoutException;
        }
    }
}
