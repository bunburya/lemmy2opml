# lemmy2opml

`lemmy2opml.py` is a tool to export and import subscriptions to Lemmy communities. It allows Lemmy users to easily
follow their subscribed communities using an RSS feed reader, and to back up and share their subscribed communities.

Subscriptions are exported to an [OPML](http://opml.org/) file. The OPML file can be imported by most RSS feed readers.
It can also be imported by `lemmy2opml.py` itself, which will then subscribe the given user to the relevant communities.

# Download and run

`lemmy2opml` is a single Python script. It should run on Python 3.9 or higher. It requires the following Python
libraries to be installed:

- [opyml](https://pypi.org/project/opyml/) for OPML file handling
- [requests](https://pypi.org/project/requests/) for making requests to the Lemmy API

To download and run `lemmy2opml`:
```commandline
git clone https://github.com/bunburya/lemmy2opml.git
cd lemmy2opml
python ./lemmy2opml.py
```

## Basic usage

### Export

To export a user's subscribed communities, basic usage is as follows:

`lemmy2opml.py export <instance_URL> <username> <output_file>`

Where:

- `<instance_URL>` is the URL for the Lemmy instance where you have an account, eg, `lemmy.ml` or
  `https://programming.dev`.
- `<username>` is your username on that instance.
- `<output_file>` is where you want the resulting OPML file to be saved.

For example:
```commandline
lemmy2opml.py export --categories --title "Example OPML file" --include-date programming.dev bba example.opml
```
will produce an OPML file similar to `example.opml`, containing the communities subscribed to by user `bba` on the
`programming.dev` instance.

### Import

To subscribe a user to a list of Lemmy communities contained in an OPML file, basic usage is:

`lemmy2opml.py <instance_URL> <username> import <input_file>`

Where `<input_file>` is the path to the OPML file you want to import.

`lemmy2opml` will wait about half a second between each subscription request, as Lemmy's API is rate-limited. Therefore,
subscribing to a large number of communities can take a bit of time. By default, `lemmy2opml` is silent unless it
encounters some issue; if you want more feedback, you can pass the `--debug` argument for more verbose logging.

For example:
```commandline
lemmy2opml.py import programming.dev bba example.opml
```
will subscribe user `bba` on instance `programming.dev` to each of the communities listed in the OPML file at
`example.opml` (assuming they are all reachable and federated with the user's instance, etc).

### Authentication

You need to provide your password so that `lemmy2opml` can get your subscribed communities or subscribe you to new ones.
**Always be careful when providing your password to third party software, and note you do so at your own risk.** You can
view the source code of `lemmy2opml.py` to see exactly what it does with your password.

By default, `lemmy2opml` will ask you to provide your password in the terminal (your input will be hidden from view).
Alternatively, you can provide your password as a separate command line argument (`--password`), or you can store your
password in a file and pass the path to that file as a command line argument (`--pass-file`). Make sure to pass these
before `import` or `export` on the command line.

### Customisation

You can customise `lemmy2opml`'s behaviour using a number of optional command line arguments. For further information on
available command line arguments run:
- `lemmy2opml.py -h`
- `lemmy2opml.py export -h`
- `lemmy2opml.py import -h`

## Development

`lemmy2opml` is written in Python and published under the MIT licence. I have only done some light testing so if you do
encounter any bugs or other issues please file an issue with as much information as possible.